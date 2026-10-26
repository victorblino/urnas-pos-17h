"""Download official 2026 second-round ballot-box logs from resultados.tse.jus.br."""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import islice
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "raw" / "portal"
BASE_URL = "https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna"
PROCESS_ID = "3221"
UFS = (
    "ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma", "mg", "ms", "mt", "pa",
    "pb", "pe", "pi", "pr", "rj", "rn", "ro", "rr", "rs", "sc", "se", "sp", "to", "zz",
)


@dataclass(frozen=True)
class Section:
    uf: str
    municipality: str
    zone: str
    section: str


def request_bytes(url: str, attempts: int = 4) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "urnas-pos-17h/1.0"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.read()
        except (TimeoutError, urllib.error.URLError):
            if attempt == attempts - 1:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def decode_jws(content: bytes) -> dict:
    try:
        payload = content.decode("ascii").split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Resposta JWS inválida do TSE") from error


def fetch_jws(url: str) -> dict:
    return decode_jws(request_bytes(url))


def index_url(uf: str) -> str:
    return f"{BASE_URL}/{PROCESS_ID}/config/{uf}/{uf}-p00{PROCESS_ID}-cs.jws"


def sections_from_index(data: dict, uf: str) -> list[Section]:
    if data.get("cdp") != PROCESS_ID:
        raise ValueError(f"Índice de {uf.upper()} não pertence ao 2º turno de 2026")

    sections = []
    for scope in data.get("abr", []):
        if scope.get("cd", "").lower() != uf:
            continue
        for municipality in scope.get("mu", []):
            for zone in municipality.get("zon", []):
                sections.extend(
                    Section(uf, municipality["cd"], zone["cd"], item["ns"])
                    for item in zone.get("sec", [])
                    if item.get("da")
                )
    return sections


def auxiliary_url(item: Section) -> str:
    prefix = f"p00{PROCESS_ID}-{item.uf}-m{item.municipality}-z{item.zone}-s{item.section}-aux.jws"
    return (
        f"{BASE_URL}/{PROCESS_ID}/dados/{item.uf}/{item.municipality}/"
        f"{item.zone}/{item.section}/{prefix}"
    )


def log_from_auxiliary(data: dict) -> tuple[str, str]:
    candidates = []
    for transmission in data.get("hashes", []):
        log = next((file for file in transmission.get("arq", []) if file.get("tp") == "log"), None)
        if log:
            received_at = datetime.strptime(
                f'{transmission.get("dr", "")} {transmission.get("hr", "")}',
                "%d/%m/%Y %H:%M:%S",
            )
            candidates.append((received_at, transmission["hash"], log["nm"]))
    if not candidates:
        raise ValueError("Manifesto sem log recebido em 25/10/2026")
    _, hash_value, filename = max(candidates)
    return hash_value, filename


def log_url(item: Section, hash_value: str, filename: str) -> str:
    return (
        f"{BASE_URL}/{PROCESS_ID}/dados/{item.uf}/{item.municipality}/"
        f"{item.zone}/{item.section}/{hash_value}/{filename}"
    )


def download_section(item: Section, output: Path) -> dict:
    filename = f"o0{PROCESS_ID}{item.uf}{item.municipality}{item.zone}{item.section}-log.jez"
    destination = output / item.uf / item.municipality / item.zone / item.section / filename
    if destination.exists() and destination.stat().st_size:
        return {**asdict(item), "status": "skipped", "path": str(destination)}

    manifest_url = auxiliary_url(item)
    hash_value, filename = log_from_auxiliary(fetch_jws(manifest_url))
    url = log_url(item, hash_value, filename)
    destination = output / item.uf / item.municipality / item.zone / item.section / filename

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(request_bytes(url))
    os.replace(temporary, destination)
    return {**asdict(item), "status": "downloaded", "path": str(destination), "url": url}


def attempt_download(item: Section, output: Path) -> dict:
    try:
        return download_section(item, output)
    except (OSError, ValueError, urllib.error.URLError) as error:
        return {**asdict(item), "status": "failed", "error": str(error)}


def bounded_downloads(executor: ThreadPoolExecutor, sections: list[Section], output: Path, workers: int):
    remaining = iter(sections)
    pending = {
        executor.submit(attempt_download, item, output)
        for item in islice(remaining, workers * 2)
    }
    while pending:
        finished, pending = wait(pending, return_when=FIRST_COMPLETED)
        for future in finished:
            yield future.result()
            item = next(remaining, None)
            if item is not None:
                pending.add(executor.submit(attempt_download, item, output))


def download_state(uf: str, output: Path, workers: int, limit: int | None, dry_run: bool) -> tuple[int, int]:
    sections = sections_from_index(fetch_jws(index_url(uf)), uf)
    if limit is not None:
        sections = sections[:limit]
    print(f"{uf.upper()}: {len(sections):,} seções oficiais encontradas")
    if dry_run:
        return len(sections), 0

    manifest_path = output / f"manifest-{uf}.jsonl"
    downloaded = skipped = failures = 0
    with manifest_path.open("a", encoding="utf-8") as manifest:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = bounded_downloads(executor, sections, output, workers)
            for position, result in enumerate(results, 1):
                downloaded += result["status"] == "downloaded"
                skipped += result["status"] == "skipped"
                failures += result["status"] == "failed"
                manifest.write(json.dumps(result, ensure_ascii=False) + "\n")
                manifest.flush()
                if position % 100 == 0 or position == len(sections):
                    print(
                        f"{uf.upper()}: {position:,}/{len(sections):,} "
                        f"({downloaded:,} novos, {skipped:,} existentes, {failures:,} falhas)",
                        flush=True,
                    )
    return len(sections), failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uf", required=True, choices=(*UFS, "all"), help="UF em minúsculas ou 'all'.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    parser.add_argument("--limit", type=int, help="Limita seções por UF para validação.")
    parser.add_argument("--dry-run", action="store_true", help="Apenas enumera as seções, sem baixar logs.")
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit deve ser maior que zero")

    args.output.mkdir(parents=True, exist_ok=True)
    states = UFS if args.uf == "all" else (args.uf,)
    total = failures = 0
    for uf in states:
        state_total, state_failures = download_state(uf, args.output, args.workers, args.limit, args.dry_run)
        total += state_total
        failures += state_failures
    print(f"Concluído: {total:,} seções; {failures:,} falhas.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
