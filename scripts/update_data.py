"""Build the public summary from official TSE second-round ballot-box logs."""

from __future__ import annotations

import io
import argparse
import hashlib
import json
import re
import sys
import tempfile
import urllib.request
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public" / "data" / "summary.json"
STATES_OUTPUT = ROOT / "public" / "data" / "states"
PORTAL_INPUT = ROOT / "data" / "raw" / "portal"
PORTAL_STATE = ROOT / "data" / "raw" / ".update-state.json"
CKAN_API = (
    "https://dadosabertos.tse.jus.br/api/3/action/package_show"
    "?id=resultados-2026-arquivos-transmitidos-para-totalizacao"
)
CATALOG_URL = (
    "https://dadosabertos.tse.jus.br/dataset/"
    "resultados-2026-arquivos-transmitidos-para-totalizacao"
)
ELECTION_DATE = "25/10/2026"
VOTE_MESSAGE = "O voto do eleitor foi computado"
CLOSING_MESSAGE = "Inicio do Encerramento"
DECISION_TIME = "17:03:00"
DECISION_MINUTE = 3
TOTAL_SECOND_ROUND_URNS = 499_248
MAX_STATE_CHUNK_BYTES = 20 * 1024 * 1024
READY_MESSAGES = (
    "Aguardando digitação do título",
    "Aguardando digitação do identificador do eleitor",
)
OFFICIAL_OFFSET = {"AC": -2, "AM": -1, "MS": -1, "MT": -1, "RO": -1, "RR": -1}
LOG_NAME = re.compile(r"o03221([a-z]{2})(\d{5})(\d{4})(\d{4})-log\.jez$", re.IGNORECASE)
PIPELINE_VERSION = 1


@dataclass
class LogSummary:
    municipality: str
    zone: str
    section: str
    votes: int
    after_decision_by_offset: dict[int, int]
    inferred_offset: int
    closing: int | None
    last_vote: int | None


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "urnas-pos-17h/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "urnas-pos-17h/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as target:
        while chunk := response.read(1024 * 1024):
            target.write(chunk)


def clock_seconds(value: str) -> int:
    hour, minute, second = (int(part) for part in value.split(":"))
    return hour * 3600 + minute * 60 + second


def infer_offset(opening: int | None, uf: str) -> int:
    expected = OFFICIAL_OFFSET.get(uf, 0)
    if opening is None:
        return expected
    observed = opening / 3600 - 8
    if abs(observed - expected) <= 0.75:
        return expected
    return max(-3, min(2, round(observed)))


def parse_log(name: str, content: bytes, uf: str) -> LogSummary | None:
    match = LOG_NAME.search(name)
    if not match or match.group(1).upper() != uf.upper():
        return None

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        log_name = next((item for item in archive.namelist() if item.lower().endswith("logd.dat")), None)
        if not log_name:
            return None
        text = archive.read(log_name).decode("latin-1")

    votes: list[int] = []
    opening: int | None = None
    closing: int | None = None
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 5 or parts[0][:10] != ELECTION_DATE or parts[3] != "VOTA":
            continue
        moment = clock_seconds(parts[0][11:19])
        message = parts[4]
        if message.startswith(READY_MESSAGES):
            opening = moment if opening is None else min(opening, moment)
        elif message == VOTE_MESSAGE:
            votes.append(moment)
        elif message == CLOSING_MESSAGE:
            closing = moment if closing is None else min(closing, moment)

    _, municipality, zone, section = match.groups()
    return LogSummary(
        municipality=municipality,
        zone=zone,
        section=section,
        votes=len(votes),
        after_decision_by_offset={
            offset: sum(value >= (17 + offset) * 3600 + DECISION_MINUTE * 60 for value in votes)
            for offset in range(-3, 3)
        },
        inferred_offset=infer_offset(opening, uf),
        closing=closing,
        last_vote=max(votes, default=None),
    )


def format_clock(value: int | None) -> str | None:
    if value is None:
        return None
    value %= 24 * 3600
    return f"{value // 3600:02d}:{value % 3600 // 60:02d}:{value % 60:02d}"


def aggregate_state(uf: str, logs: list[LogSummary], errors: int = 0) -> tuple[dict, list[dict]]:
    offsets_by_municipality: dict[str, Counter[int]] = defaultdict(Counter)
    for log in logs:
        offsets_by_municipality[log.municipality][log.inferred_offset] += 1
    municipality_offset = {
        municipality: counts.most_common(1)[0][0]
        for municipality, counts in offsets_by_municipality.items()
    }

    sections = []
    for log in logs:
        offset = municipality_offset.get(log.municipality, OFFICIAL_OFFSET.get(uf, 0))
        closing_brasilia = log.closing - offset * 3600 if log.closing is not None else None
        last_vote_brasilia = log.last_vote - offset * 3600 if log.last_vote is not None else None
        after = log.after_decision_by_offset[offset]
        sections.append({
            "municipality": log.municipality,
            "zone": log.zone,
            "section": log.section,
            "votes": log.votes,
            "beforeDecision": log.votes - after,
            "afterDecision": after,
            "percentageAfterDecision": round(after / log.votes * 100, 4) if log.votes else 0,
            "timezoneOffset": offset,
            "closingStartedAtLocal": format_clock(log.closing),
            "closingStartedAtBrasilia": format_clock(closing_brasilia),
            "lastVoteAtLocal": format_clock(log.last_vote),
            "lastVoteAtBrasilia": format_clock(last_vote_brasilia),
        })

    voters = sum(item["votes"] for item in sections)
    after = sum(item["afterDecision"] for item in sections)
    closing_times = [
        item["closingStartedAtBrasilia"]
        for item in sections
        if item["closingStartedAtBrasilia"] is not None
    ]
    closed_before_decision = sum(
        item["closingStartedAtBrasilia"] is not None
        and item["closingStartedAtBrasilia"] < DECISION_TIME
        for item in sections
    )
    state = {
        "uf": uf,
        "votersFromLogs": voters,
        "beforeDecision": voters - after,
        "afterDecision": after,
        "percentageAfterDecision": round(after / voters * 100, 4) if voters else 0,
        "sectionsProcessed": len(sections),
        "sectionsWithVotesAfterDecision": sum(item["afterDecision"] > 0 for item in sections),
        "sectionsClosedBeforeDecision": closed_before_decision,
        "sectionsWithoutClosingRecord": sum(item["closingStartedAtLocal"] is None for item in sections),
        "earliestClosingStartedAtBrasilia": min(closing_times, default=None),
        "latestClosingStartedAtBrasilia": max(closing_times, default=None),
        "logsRejected": errors,
    }
    return state, sorted(sections, key=lambda item: (-item["afterDecision"], item["municipality"], item["zone"], item["section"]))


def summarize_state(uf: str, zip_path: Path) -> dict:
    logs: list[LogSummary] = []
    errors = 0
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith("-log.jez"):
                continue
            try:
                summary = parse_log(name, archive.read(name), uf)
                if summary:
                    logs.append(summary)
            except (OSError, ValueError, zipfile.BadZipFile):
                errors += 1

    state, _ = aggregate_state(uf, logs, errors)
    return state


def portal_logs(directory: Path) -> dict[str, list[LogSummary]]:
    by_state: dict[str, list[LogSummary]] = defaultdict(list)
    for path in directory.rglob("*-log.jez"):
        match = LOG_NAME.search(path.name)
        if not match:
            continue
        uf = match.group(1).upper()
        try:
            summary = parse_log(path.name, path.read_bytes(), uf)
            if summary:
                by_state[uf].append(summary)
        except (OSError, ValueError, zipfile.BadZipFile):
            continue
    return by_state


def portal_snapshot(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*-log.jez")):
        stat = path.stat()
        digest.update(f"{path.relative_to(directory)}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def portal_is_current(snapshot: str) -> bool:
    try:
        saved = json.loads(PORTAL_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return saved == {"pipelineVersion": PIPELINE_VERSION, "snapshot": snapshot}


def save_portal_snapshot(snapshot: str) -> None:
    PORTAL_STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PORTAL_STATE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"pipelineVersion": PIPELINE_VERSION, "snapshot": snapshot}) + "\n",
        encoding="utf-8",
    )
    temporary.replace(PORTAL_STATE)


def state_chunks(sections: list[dict], max_bytes: int = MAX_STATE_CHUNK_BYTES) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    chunk: list[dict] = []
    chunk_size = len(b'{"sections":[]}')
    for section in sections:
        size = len(json.dumps(section, ensure_ascii=False, separators=(",", ":")).encode()) + 1
        if chunk and chunk_size + size > max_bytes:
            chunks.append(chunk)
            chunk = []
            chunk_size = len(b'{"sections":[]}')
        chunk.append(section)
        chunk_size += size
    return chunks + ([chunk] if chunk else [])


def write_payload(states: list[dict], source: dict, details: dict[str, list[dict]] | None = None) -> None:
    voters = sum(state["votersFromLogs"] for state in states)
    after = sum(state["afterDecision"] for state in states)
    earliest_closings = [state["earliestClosingStartedAtBrasilia"] for state in states if state["earliestClosingStartedAtBrasilia"]]
    latest_closings = [state["latestClosingStartedAtBrasilia"] for state in states if state["latestClosingStartedAtBrasilia"]]
    payload = {
        "status": "complete" if len(states) == 28 else "partial",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "electionDate": "2026-10-25",
        "round": 2,
        "tseProcessId": "3221",
        "tseElectionId": "6258",
        "cutoff": f"2026-10-25T{DECISION_TIME}-03:00",
        "cutoffLabel": "Divulgação da decisão de prorrogação da votação",
        "nationwide": {
            "votersFromLogs": voters,
            "beforeDecision": voters - after,
            "afterDecision": after,
            "percentageAfterDecision": round(after / voters * 100, 4) if voters else 0,
            "sectionsProcessed": sum(state["sectionsProcessed"] for state in states),
            "sectionsExpected": TOTAL_SECOND_ROUND_URNS,
            "sectionsCoveragePercentage": round(
                sum(state["sectionsProcessed"] for state in states) / TOTAL_SECOND_ROUND_URNS * 100,
                4,
            ),
            "sectionsWithVotesAfterDecision": sum(state["sectionsWithVotesAfterDecision"] for state in states),
            "sectionsClosedBeforeDecision": sum(
                state["sectionsClosedBeforeDecision"] for state in states
            ),
            "sectionsWithoutClosingRecord": sum(state["sectionsWithoutClosingRecord"] for state in states),
            "earliestClosingStartedAtBrasilia": min(earliest_closings, default=None),
            "latestClosingStartedAtBrasilia": max(latest_closings, default=None),
        },
        "states": sorted(states, key=lambda state: state["uf"]),
        "source": source,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if details:
        STATES_OUTPUT.mkdir(parents=True, exist_ok=True)
        for uf, sections in details.items():
            state = next(item for item in states if item["uf"] == uf)
            chunks = state_chunks(sections)
            state_directory = STATES_OUTPUT / uf.lower()
            state_directory.mkdir(parents=True, exist_ok=True)
            content = {"state": state, "chunks": [f"{uf.lower()}/{index}.json" for index in range(len(chunks))]}
            (STATES_OUTPUT / f"{uf.lower()}.json").write_text(
                json.dumps(content, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            for index, chunk in enumerate(chunks):
                (state_directory / f"{index}.json").write_text(
                    json.dumps({"sections": chunk}, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )


def local_archives(directory: Path) -> list[tuple[str, Path]]:
    archives = []
    for path in sorted(directory.glob("*.zip")):
        match = re.search(r"(?:^|[_-])([A-Za-z]{2})\.zip$", path.name)
        if "2t" not in path.stem.lower():
            continue
        if match:
            archives.append((match.group(1).upper(), path))
    return archives


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="Processa ZIPs oficiais do 2º turno, mantendo '2t' e a UF no nome.",
    )
    parser.add_argument(
        "--portal-dir",
        type=Path,
        default=PORTAL_INPUT,
        help="Processa os arquivos .jez baixados do portal oficial do TSE.",
    )
    parser.add_argument("--force", action="store_true", help="Reprocessa os logs do portal mesmo sem arquivos novos.")
    args = parser.parse_args()

    snapshot = portal_snapshot(args.portal_dir) if args.portal_dir.exists() else None
    if snapshot and not args.input_dir and not args.force and portal_is_current(snapshot):
        print("Nenhum log novo ou alterado no portal.")
        return 0

    downloaded = portal_logs(args.portal_dir) if args.portal_dir.exists() else {}
    if downloaded and not args.input_dir:
        states = []
        details = {}
        for uf, logs in sorted(downloaded.items()):
            print(f"Processando {uf}: {len(logs):,} logs do 2º turno...", flush=True)
            state, sections = aggregate_state(uf, logs)
            states.append(state)
            details[uf] = sections
        write_payload(states, {
            "publisher": "Tribunal Superior Eleitoral",
            "processId": "3221",
            "electionId": "6258",
            "resultsUrl": "https://resultados.tse.jus.br/oficial/app/index.html#/eleicao/6258/uf/br/cargo/1/vis/nominal/dados-de-urna/boletim-de-urna",
            "logBaseUrl": "https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221/dados",
        }, details)
        if snapshot:
            save_portal_snapshot(snapshot)
        print(f"Resumo gravado em {OUTPUT}")
        return 0

    package = fetch_json(CKAN_API)["result"]
    resources = [
        resource
        for resource in package["resources"]
        if "Segundo turno" in resource["name"]
        and resource["url"].startswith("https://cdn.tse.jus.br/")
    ]
    archives = local_archives(args.input_dir) if args.input_dir else []
    if args.input_dir and not archives:
        print(f"Nenhum ZIP por UF encontrado em {args.input_dir}.", file=sys.stderr)
        return 2
    if not resources and not archives:
        print("O TSE ainda não publicou os ZIPs oficiais do segundo turno.", file=sys.stderr)
        return 2

    states = []
    if archives:
        for uf, path in archives:
            print(f"Processando {uf} de {path.name}...", flush=True)
            states.append(summarize_state(uf, path))
    else:
        with tempfile.TemporaryDirectory(prefix="urnas-pos-17h-") as temp_dir:
            for resource in resources:
                uf = resource["name"][:2].upper()
                path = Path(temp_dir) / f"{uf}.zip"
                print(f"Baixando e processando {uf}...", flush=True)
                download(resource["url"], path)
                states.append(summarize_state(uf, path))

    write_payload(states, {
        "publisher": "Tribunal Superior Eleitoral",
        "dataset": package["title"],
        "catalogUrl": CATALOG_URL,
        "apiUrl": CKAN_API,
        "resources": [resource["url"] for resource in resources],
    })
    print(f"Resumo gravado em {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
