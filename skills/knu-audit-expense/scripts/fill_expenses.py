#!/usr/bin/env python3
"""Fill expense-processing tables in an existing HWP with rhwp."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


FUND_DIGITS = {
    "1": "1",
    "총학생회비": "1",
    "운영비": "1",
    "4": "4",
    "자체수익금": "4",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        detail = result.stderr.strip() or "command failed"
        fail(f"rhwp 오류: {detail}")
    return result.stdout if capture else ""


def find_rhwp(explicit: str | None) -> str:
    candidate = explicit or os.environ.get("KNU_AUDIT_RHWP_BIN") or shutil.which("rhwp")
    if not candidate or not Path(candidate).is_file():
        fail("rhwp를 찾지 못했습니다. --rhwp 또는 KNU_AUDIT_RHWP_BIN으로 경로를 지정하세요.")
    return str(Path(candidate).resolve())


def normalize_date(value: object, *, trailing_dot: bool = False) -> str:
    if isinstance(value, (date, datetime)):
        raw = value.strftime("%Y.%m.%d")
    else:
        raw = str(value).strip().replace("-", ".").rstrip(".")
    match = re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", raw)
    if not match:
        fail(f"날짜 형식 오류: {value!r}")
    year, month, day = map(int, match.groups())
    try:
        parsed = date(year, month, day)
    except ValueError as error:
        fail(f"날짜 오류: {value!r} ({error})")
    normalized = parsed.strftime("%Y.%m.%d")
    return normalized + ("." if trailing_dot else "")


def positive_amount(value: object) -> int:
    if isinstance(value, str):
        value = re.sub(r"[^0-9-]", "", value)
    try:
        amount = int(value)
    except (TypeError, ValueError):
        fail(f"금액 형식 오류: {value!r}")
    if amount <= 0:
        fail(f"금액은 양수여야 합니다: {amount}")
    return amount


def receipt_suffix(item: dict, semester: int) -> str:
    direct = str(item.get("receipt_suffix", "")).strip()
    if direct:
        if not re.fullmatch(r"\d{5}", direct):
            fail(f"receipt_suffix는 숫자 5자리여야 합니다: {direct!r}")
        return direct
    code = str(item.get("item_code", "")).strip()
    if not re.fullmatch(r"\d{3}", code):
        fail(f"item_code는 숫자 3자리여야 합니다: {code!r}")
    fund = str(item.get("fund", "")).strip()
    if item.get("expense_type") == "철거비":
        fund = "자체수익금"
    digit = FUND_DIGITS.get(fund)
    if not digit:
        fail(f"알 수 없는 재원: {fund!r}")
    return f"{semester}{digit}{code}"


def required_text(item: dict, key: str) -> str:
    value = str(item.get(key, "")).strip()
    if not value:
        fail(f"필수 값이 없습니다: {key}")
    return value


def account_name(item: dict, key: str) -> str:
    """Return only the account name, even if a code was supplied with it."""
    value = required_text(item, key)
    name = re.sub(r"^\d{3,5}\s*[-.:)]?\s*", "", value).strip()
    if not name:
        fail(f"{key}에는 코드가 아닌 명칭이 필요합니다: {value!r}")
    return name


def normalize_items(payload: dict) -> list[dict]:
    semester = int(payload.get("semester", 2))
    if semester not in (1, 2):
        fail("semester는 1 또는 2여야 합니다.")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        fail("items 배열이 비어 있습니다.")

    groups: OrderedDict[tuple[str, ...], dict] = OrderedDict()
    for index, source in enumerate(items, 1):
        if not isinstance(source, dict):
            fail(f"items[{index}]는 객체여야 합니다.")
        item = dict(source)
        suffix = receipt_suffix(item, semester)
        payment_date = normalize_date(item.get("payment_date"))
        category = account_name(item, "category")
        subcategory = account_name(item, "subcategory")
        transaction = str(item.get("transaction_id") or f"item-{index}")
        key = (transaction, payment_date, category, subcategory, suffix)
        if key not in groups:
            vendor = required_text(item, "vendor")
            purpose = required_text(item, "purpose")
            if not purpose.endswith("."):
                purpose += "."
            note = str(item.get("note") or f"{vendor}에서 구매함.").strip()
            if not note.endswith("."):
                note += "."
            groups[key] = {
                "category": category,
                "subcategory": subcategory,
                "receipt_suffix": suffix,
                "payment_date": payment_date,
                "amount": 0,
                "purpose": purpose,
                "note": note,
                "label": str(item.get("label") or suffix).strip(),
            }
        groups[key]["amount"] += positive_amount(item.get("amount"))
    return list(groups.values())


def export_tables(rhwp: str, source: Path) -> dict:
    raw = run([rhwp, "export-tables", str(source), "--json"], capture=True)
    return json.loads(raw)


def expense_table(document: dict) -> dict:
    for table in document.get("tables", []):
        labels = {cell.get("text", "").strip() for cell in table.get("cells", [])}
        if "영수증코드" in labels and "결제일자" in labels and "비고" in labels:
            return table
    fail("비용처리서 표를 찾지 못했습니다.")


def row_cells(table: dict, label: str) -> list[dict]:
    cells = table.get("cells", [])
    label_cell = next((cell for cell in cells if cell.get("text", "").strip() == label), None)
    if not label_cell:
        fail(f"양식에서 {label!r} 행을 찾지 못했습니다.")
    return sorted(
        (cell for cell in cells if cell.get("row") == label_cell.get("row")),
        key=lambda cell: cell.get("col", 0),
    )


def locate_targets(table: dict) -> dict[str, list[tuple[int, int]]]:
    def after_label(label: str) -> tuple[int, int]:
        cells = row_cells(table, label)
        if len(cells) < 2:
            fail(f"{label!r} 입력 칸을 찾지 못했습니다.")
        return cells[1]["row"], cells[1]["col"]

    name_cells = row_cells(table, "비용명칭(관 또는 관·항)")
    if len(name_cells) < 3:
        fail("관·항 입력 칸을 찾지 못했습니다.")
    code_cells = [
        cell
        for cell in row_cells(table, "영수증코드")
        if str(cell.get("text", "")).strip().endswith(">")
    ]
    if not code_cells:
        fail("영수증 코드 입력 칸을 찾지 못했습니다.")
    return {
        "category": [(name_cells[1]["row"], name_cells[1]["col"])],
        "subcategory": [(name_cells[2]["row"], name_cells[2]["col"])],
        "receipt_suffix": [(cell["row"], cell["col"]) for cell in code_cells],
        "payment_date": [after_label("결제일자")],
        "amount": [after_label("금액")],
        "purpose": [after_label("비용처리목적")],
        "note": [after_label("비고")],
        "written_date": [after_label("작성일자")],
    }


def safe_label(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", value).strip("_.")
    return cleaned[:40] or "항목"


def fill_one(
    rhwp: str,
    template: Path,
    output: Path,
    table_index: int,
    table_count: int,
    targets: dict[str, list[tuple[int, int]]],
    form: dict,
    written_date: str,
) -> None:
    values = {
        "category": form["category"],
        "subcategory": form["subcategory"],
        "receipt_suffix": form["receipt_suffix"] + ">",
        "payment_date": form["payment_date"],
        "amount": f"{form['amount']:,}원",
        "purpose": form["purpose"],
        "note": form["note"],
        "written_date": written_date,
    }
    with tempfile.TemporaryDirectory(prefix="knu-audit-hwp-") as temp_dir:
        current = Path(temp_dir) / "00.hwp"
        shutil.copy2(template, current)
        step = 0
        for key, positions in targets.items():
            for row, col in positions:
                step += 1
                next_file = Path(temp_dir) / f"{step:02d}.hwp"
                run(
                    [
                        rhwp,
                        "edit",
                        "set-cell",
                        str(current),
                        "--table",
                        str(table_index),
                        "--row",
                        str(row),
                        "--col",
                        str(col),
                        "--text",
                        values[key],
                        "--keep-style",
                        "-o",
                        str(next_file),
                        "--verify",
                        "--json",
                    ]
                )
                current = next_file
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, output)

    run([
        rhwp,
        "verify",
        str(output),
        "--expect-table-count",
        str(table_count),
        "--json",
    ])
    reread = expense_table(export_tables(rhwp, output))
    all_text = [str(cell.get("text", "")).strip() for cell in reread.get("cells", [])]
    for expected in values.values():
        if expected not in all_text:
            fail(f"재독 검증 실패 ({output.name}): {expected!r}")
    expected_code_cells = len(targets["receipt_suffix"])
    if all_text.count(values["receipt_suffix"]) != expected_code_cells:
        fail(f"영수증 코드 범위 검증 실패 ({output.name})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_json", type=Path)
    parser.add_argument("--rhwp", help="rhwp 실행 파일 경로")
    args = parser.parse_args()

    payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    template = Path(payload.get("template", "")).expanduser().resolve()
    if not template.is_file():
        fail(f"양식 파일을 찾지 못했습니다: {template}")
    output_dir = Path(payload.get("output_dir") or "output").expanduser().resolve()
    written_date = normalize_date(
        payload.get("written_date") or datetime.now(ZoneInfo("Asia/Seoul")).date(),
        trailing_dot=True,
    )
    rhwp = find_rhwp(args.rhwp)
    document = export_tables(rhwp, template)
    table = expense_table(document)
    targets = locate_targets(table)
    forms = normalize_items(payload)

    created = []
    filename_counts: dict[str, int] = {}
    for form in forms:
        base = f"비용처리서_도26-{form['receipt_suffix']}_{safe_label(form['label'])}"
        filename_counts[base] = filename_counts.get(base, 0) + 1
        number = filename_counts[base]
        filename = f"{base}{f'_{number}' if number > 1 else ''}.hwp"
        destination = output_dir / filename
        fill_one(
            rhwp,
            template,
            destination,
            int(table["index"]),
            int(document["tableCount"]),
            targets,
            form,
            written_date,
        )
        created.append(str(destination))
    print(json.dumps({"created": created}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
