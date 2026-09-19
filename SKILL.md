---
name: knu-audit-expense
description: "영수증·거래명세서·구매내역과 사용자가 제공한 관·항·목 코드를 읽어 경북대학교 감사 비용처리서 HWP를 작성한다. 기존 HWP 양식을 그대로 유지하면서 비용처리서 표만 채우거나, 같은 목의 금액을 합산해 목별 파일을 만들 때 사용한다."
---

# 감사 비용처리서 HWP

## 원칙

- 사용자가 준 HWP 전체를 복사해 비용처리서 표만 수정한다. 페이지 추출·합본·다른 서식 수정은 하지 않는다.
- 증빙의 감사 적격성이나 실물 제출 요건은 판단·경고하지 않는다.
- 같은 거래에서 같은 목은 합산하고, 다른 목은 각각 별도 HWP로 만든다.
- 작성일자는 별도 지시가 없으면 Asia/Seoul 기준 실행 당일이다.
- 관·항·목은 제공 자료를 우선한다. 작성에 꼭 필요한 값만 없을 때 짧게 질문한다.

세부 규칙은 [rules.md](references/rules.md)를 필요할 때만 읽는다.

## 실행

1. 입력 자료에서 결제일자, 금액, 관, 항, 목 코드, 재원, 구매처, 용도를 추출한다.
2. 같은 거래·같은 목 항목을 합산해 JSON을 만든다. 서로 다른 목은 별도 항목으로 둔다.
3. `rhwp` 실행 파일을 준비한다. PATH에 없으면 공식 Releases의 현재 플랫폼용 바이너리를 작업 공간에 내려받고 `--rhwp`로 지정한다. 소스 저장소 전체를 복제하거나 빌드하지 않는다.
4. 다음처럼 작성한다.

```bash
python3 scripts/fill_expenses.py input.json --rhwp /path/to/rhwp
```

5. 생성된 HWP를 다시 읽어 입력값이 정확한지 확인한다. 스크립트가 이 검사를 자동 수행한다.
6. 새 학기 양식을 처음 쓰거나 긴 문구를 넣었을 때만 `rhwp layout-anomaly`와 렌더링으로 밀림·넘침을 확인한다.

## JSON

```json
{
  "template": "/path/to/template.hwp",
  "output_dir": "/path/to/output",
  "items": [
    {
      "transaction_id": "receipt-1",
      "category": "관 명칭",
      "subcategory": "항 명칭",
      "item_code": "211",
      "fund": "총학생회비",
      "payment_date": "2026.09.19",
      "amount": 30000,
      "purpose": "사무실 비품으로 사용하기 위함.",
      "vendor": "쿠팡",
      "label": "건전지"
    }
  ]
}
```

`receipt_suffix`가 있으면 재원·목 조합보다 우선한다. `note`가 없으면 `{vendor}에서 구매함.`을 쓴다.

이 스킬은 범용 에이전트 포장인 `claw-hwp` 대신 그 기반 엔진인 `rhwp` CLI를 직접 호출한다. 중복 스킬을 로드하지 않아 실행 경로와 문맥 사용량을 줄인다.
