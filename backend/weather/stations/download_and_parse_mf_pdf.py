import argparse
import csv
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypeAlias

import pdfplumber
import requests
from pdfminer.pdfexceptions import PDFException
from pdfplumber.utils.exceptions import PdfminerException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

DEFAULT_MAX_PDFS_TO_READ: int | None = None

StationCode: TypeAlias = str


@dataclass
class TemperatureClass:
    classe: str | None = None
    debut: str | None = None
    fin: str | None = None


@dataclass
class StationInfo:
    station_code: StationCode
    url: str
    departement: str | None
    creation_date: str | None
    classes_temperature: list[TemperatureClass]


ITN_STATIONS_IDS = (
    "06088001",
    "13054001",
    "14137001",
    "16089001",
    "20148001",
    "21473001",
    "25056001",
    "26198001",
    "29075001",
    "30189001",
    "31069001",
    "33281001",
    "35281001",
    "36063001",
    "44020001",
    "45055001",
    "47091001",
    "51183001",
    "51449002",
    "54526001",
    "58160001",
    "59343001",
    "63113001",
    "64549001",
    "66136001",
    "67124001",
    "69029001",
    "72181001",
    "73054001",
    "75114001",
    "86027001",
)


def get_pdf_urls_cache_path(script_dir: Path) -> Path:
    return script_dir / "pdf_urls.json"


def load_cached_pdf_urls(cache_file_path: Path) -> list[str]:
    with open(cache_file_path, encoding="utf-8") as file:
        return json.load(file)


def fetch_urls(api_url: str) -> list[str]:
    response = requests.get(api_url)
    data = response.json().get("resources", [])
    return [
        res["url"] for res in data if res.get("url") and res.get("url").endswith(".pdf")
    ]


def save_pdf_urls(pdf_urls: list[str], cache_file_path: Path) -> None:
    ensure_directory_exists(cache_file_path)
    with open(cache_file_path, mode="w", encoding="utf-8") as file:
        json.dump(pdf_urls, file, indent=4, ensure_ascii=False)


def get_pdf_urls(*, script_dir: Path, update: bool = False) -> list[str]:
    api_url = "https://www.data.gouv.fr/api/1/datasets/67a1e85a366f75613f750296/"
    cache_file_path = get_pdf_urls_cache_path(script_dir)

    if cache_file_path.exists() and not update:
        logger.debug("Reading cached PDF URLs from %s", cache_file_path)
        return load_cached_pdf_urls(cache_file_path)

    pdf_urls = fetch_urls(api_url)
    save_pdf_urls(pdf_urls, cache_file_path)
    return pdf_urls


def build_station_info_from_text(
    station_id: StationCode,
    pdf_url: str,
    text: str,
) -> StationInfo:
    classes = extract_classes(text)
    creation_date = extract_creation_date(text)
    departement_info = extract_departement(text)

    return StationInfo(
        station_code=station_id,
        url=pdf_url,
        departement=departement_info["code"],
        creation_date=creation_date,
        classes_temperature=classes,
    )


def get_meteofrance_data_dict(
    *,
    update=False,
    itn_only=False,
    max_pdfs=DEFAULT_MAX_PDFS_TO_READ,
    keep_pdf=False,
    parallelism=1,
) -> dict[StationCode, StationInfo]:
    data_dict: dict[StationCode, StationInfo] = {}
    script_dir = Path(__file__).parent
    pdf_urls = get_pdf_urls(script_dir=script_dir, update=update)
    selected_stations: list[tuple[StationCode, str, int]] = []
    seen_station_ids: set[StationCode] = set()

    for i, pdf_url in enumerate(pdf_urls, 1):
        station_id = extract_id(pdf_url)

        if (
            station_id is None
            or station_id in seen_station_ids
            or (itn_only and station_id not in ITN_STATIONS_IDS)
        ):
            continue

        seen_station_ids.add(station_id)
        selected_stations.append((station_id, pdf_url, i))

    if max_pdfs:
        selected_stations = selected_stations[:max_pdfs]

    total = len(selected_stations)

    def process_station(
        station_id: StationCode,
        pdf_url: str,
        position: int,
    ) -> tuple[StationCode, StationInfo]:
        logger.info("Processing station %s (%d/%d)", station_id, position, total)

        station_info = get_station_info(
            station_id=station_id,
            pdf_url=pdf_url,
            script_dir=script_dir,
            update=update,
            keep_pdf=keep_pdf,
        )
        return station_id, station_info

    if parallelism <= 1:
        for station_id, pdf_url, position in selected_stations:
            resolved_station_id, station_info = process_station(
                station_id=station_id,
                pdf_url=pdf_url,
                position=position,
            )
            data_dict[resolved_station_id] = station_info
        return data_dict

    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = [
            executor.submit(
                process_station,
                station_id,
                pdf_url,
                position,
            )
            for station_id, pdf_url, position in selected_stations
        ]

        for future in as_completed(futures):
            station_id, station_info = future.result()
            data_dict[station_id] = station_info

    return data_dict


def get_pdf(pdf_url: str):
    return requests.get(pdf_url)


def save_pdf(filename: Path, pdf_resp) -> None:
    with open(filename, "wb") as f:
        f.write(pdf_resp.content)


def download_and_save_pdf(pdf_url: str, filename: Path) -> None:
    pdf_resp = get_pdf(pdf_url)
    save_pdf(filename, pdf_resp)


def get_station_info(
    *,
    station_id: StationCode,
    pdf_url: str,
    script_dir: Path,
    update: bool,
    keep_pdf: bool,
) -> StationInfo:
    try:
        text = get_station_text(
            station_id=station_id,
            pdf_url=pdf_url,
            script_dir=script_dir,
            update=update,
            keep_pdf=keep_pdf,
        )
        return build_station_info_from_text(station_id, pdf_url, text)
    except (OSError, PDFException, PdfminerException):
        logger.warning("Failed to parse station %s, marking as failed", station_id)
        return StationInfo(
            station_code=station_id,
            url=pdf_url,
            departement="failed",
            creation_date="failed",
            classes_temperature=[],
        )


def get_station_text(
    *,
    station_id: StationCode,
    pdf_url: str,
    script_dir: Path,
    update: bool,
    keep_pdf: bool,
) -> str:
    txt_filename = script_dir / "txts" / f"file_{station_id}.txt"
    pdf_filename = script_dir / "pdfs" / f"file_{station_id}.pdf"

    if txt_filename.exists() and not update:
        logger.debug("Reading text from %s", txt_filename)
        return txt_filename.read_text(encoding="utf-8")

    logger.debug("Downloading %s to %s", pdf_url, pdf_filename)
    ensure_directory_exists(pdf_filename)
    download_and_save_pdf(pdf_url, pdf_filename)

    logger.debug("Extracting text from %s", pdf_filename)
    text = extract_text_from_pdf(pdf_filename)
    logger.debug("Extracted text from %s:\n%s", pdf_filename, text)

    ensure_directory_exists(txt_filename)
    txt_filename.write_text(text, encoding="utf-8")

    if not keep_pdf:
        pdf_filename.unlink(missing_ok=True)

    return text


def extract_text_from_pdf(filename: Path) -> str:
    text = ""
    with pdfplumber.open(filename) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text


def extract_id(pdf_url: str) -> StationCode | None:
    m = re.search(r"\d+/(\d+)", pdf_url)
    return m.group(1) if m else None


def date_dd_mm_yyyy_to_iso(date_str: str) -> str:
    """
    Convert date string in DD/MM/YYYY format to ISO 8601 format with +00:00.

    Args:
        date_str: Date string in DD/MM/YYYY format (e.g., "01/09/1920")

    Returns:
        ISO 8601 date string with +00:00 (e.g., "1920-09-01T00:00:00+00:00")
    """
    day, month, year = date_str.split("/")
    return f"{year}-{month}-{day}T00:00:00+00:00"


def year_to_iso(year_str: str) -> str:
    """
    Convert year string to ISO 8601 format with +00:00.

    Args:
        year_str: Year string (e.g., "1957")

    Returns:
        ISO 8601 date string with +00:00 (e.g., "1957-01-01T00:00:00+00:00")
    """
    return f"{year_str}-01-01T00:00:00+00:00"


def extract_creation_date(text: str) -> str | None:
    # -------------------------
    # Année et mois de création (returned as ISO string with +00:00)
    # -------------------------
    # Souvent sous :
    # "Ouverture : 1957"
    # "Date d'ouverture : 01/01/1957"
    patterns = [
        # "Ouverture : 1957"
        (r"Ouverture\s*:\s*(\d{4})", "year"),
        # "Date d'ouverture : 01/01/1957"
        (r"Date d['']ouverture\s*:\s*(\d{2}/\d{2}/\d{4})", "dmy"),
        # "Mise en service : 1957"
        (r"Mise en service\s*:\s*(\d{4})", "year"),
    ]

    for pattern, fmt in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        if fmt == "dmy":
            return date_dd_mm_yyyy_to_iso(match.group(1))
        if fmt == "year":
            return year_to_iso(match.group(1))
    return None


def extract_departement(text: str) -> dict:
    """
    Extrait le nom du département et le code entre parenthèses.
    Retourne un dict : {"nom": ..., "code": ...}
    """
    pattern = r"Département\s*:\s*([^\(]+)\(?([0-9]{1,3}|2A|2B)?\)?"

    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return {"nom": None, "code": None}

    nom = match.group(1).strip()  # texte avant la parenthèse
    code = match.group(2) if match.group(2) else None  # texte entre parenthèses
    return {"nom": nom, "code": code}


def extract_classes(text: str) -> list[TemperatureClass]:
    all_classes: list[TemperatureClass] = []

    section_pattern = r"QUALITE\s+DU\s+SITE(.*?)(?=\n[A-ZÉ ]{5,}|\Z)"
    sections = re.finditer(section_pattern, text, re.DOTALL)

    for section_match in sections:
        section = section_match.group(1)

        class_matches = re.findall(
            r"\bTemperature\s+(\d+)\s+\S+\s+(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}/\d{2}/\d{4}))?",
            #                  ^^^          ^^^^^^^^^^         ^^^^^^^^^^
            #                classe            Début               Fin (optional)
            section,
        )

        for c, d, f in class_matches:
            debut_iso = date_dd_mm_yyyy_to_iso(d) if d else None
            fin_iso = date_dd_mm_yyyy_to_iso(f) if f else None
            all_classes.append(TemperatureClass(classe=c, debut=debut_iso, fin=fin_iso))

    if not all_classes:
        return [TemperatureClass()]

    return all_classes


def build_csv_lines(
    station_id: StationCode, info: StationInfo
) -> list[list[str | None]]:
    base_row = [
        station_id,
        info.url,
        info.departement,
        info.creation_date,
    ]
    classes = info.classes_temperature

    return [base_row + [c.classe, c.debut, c.fin] for c in classes]


def prepare_csv_rows(
    data_dict: dict[StationCode, StationInfo],
) -> list[list[str | None]]:
    header = ["id", "url", "departement", "creation_date", "classe", "debut", "fin"]
    rows: list[list[str | None]] = [header]
    for station_id, info in data_dict.items():
        rows.extend(build_csv_lines(station_id, info))
    return rows


def ensure_directory_exists(file_path: Path) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)


def save_csv(rows: list[list[str | None]], csv_file_path: Path) -> None:
    ensure_directory_exists(csv_file_path)
    with open(csv_file_path, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        for row in rows:
            writer.writerow(row)


def dict_to_csv(data_dict: dict[StationCode, StationInfo], csv_file_path: Path) -> None:
    rows = prepare_csv_rows(data_dict)
    save_csv(rows, csv_file_path)


def serialize_data_dict(data_dict: dict[StationCode, StationInfo]) -> dict:
    return {
        station_id: {
            "station_code": info.station_code,
            "url": info.url,
            "departement": info.departement,
            "creation_date": info.creation_date,
            "classes_temperature": [asdict(tc) for tc in info.classes_temperature],
        }
        for station_id, info in data_dict.items()
    }


def save_json(data: dict, json_file_path: Path) -> None:
    ensure_directory_exists(json_file_path)
    with open(json_file_path, mode="w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)


def dict_to_json(
    data_dict: dict[StationCode, StationInfo], json_file_path: Path
) -> None:
    serializable_data = serialize_data_dict(data_dict)
    save_json(serializable_data, json_file_path)


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and parse Météo-France station PDF files.",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable DEBUG log level (default: INFO).",
    )
    verbosity.add_argument(
        "-v",
        action="count",
        dest="verbosity",
        default=0,
        help="Increase verbosity; use -vvv or more to enable DEBUG (default: INFO).",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        default=False,
        help="Refresh cached PDF URLs and re-download/re-extract station files even if text already exists locally (default: False).",
    )
    parser.add_argument(
        "--save-pdf",
        action="store_true",
        default=False,
        help="Keep downloaded PDF files after text extraction (default: False).",
    )
    parser.add_argument(
        "--itn",
        action="store_true",
        default=False,
        help="Restrict processing to ITN stations only (default: all stations).",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=None,
        dest="max_pdfs",
        help="Maximum number of PDFs to read (default: no limit).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        dest="output_dir",
        help="Output directory (relative to cwd). Default: output/ next to this script.",
    )
    parser.add_argument(
        "--parallelism",
        type=int,
        default=1,
        help="Number of stations to process in parallel (default: 1).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_cli_args()
    if args.debug or (args.verbosity is not None and args.verbosity >= 3):
        logger.setLevel(logging.DEBUG)

    if args.parallelism < 1:
        raise ValueError("--parallelism must be >= 1")

    output_dir = (
        Path.cwd() / args.output_dir
        if args.output_dir
        else Path(__file__).parent / "output"
    )

    logger.info("Started...")
    data_dict = get_meteofrance_data_dict(
        update=args.update,
        itn_only=args.itn,
        max_pdfs=args.max_pdfs,
        keep_pdf=args.save_pdf,
        parallelism=args.parallelism,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    dict_to_json(data_dict, output_dir / "stations_data.json")
    dict_to_csv(data_dict, output_dir / "stations_data.csv")

    logger.info("Data saved to %s", output_dir)


if __name__ == "__main__":
    main()
