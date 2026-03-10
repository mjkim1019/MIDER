# Issue #004: gpt-4o-mini가 PK 인덱스 비효율 패턴을 탐지하지 못함

## 문제 상황

SQL 분석 시 Explain Plan에서 **PK 인덱스를 사용하지만 Cost가 높은 INDEX RANGE SCAN**(Cost=148)이 있을 때,
조인 컬럼(`svc_mgmt_num`) 기반의 더 적합한 인덱스 힌트를 제안해야 하나 gpt-4o-mini가 이를 탐지하지 못함.

### 기대 출력
```sql
-- Line 371: m_dc_l_3 CTE
select  /*+ leading(b c) index(c (svc_mgmt_num)) "유선 할인 해지" */
```

### 실제 출력 (gpt-4o-mini)
- 4개 이슈 탐지, `ZORD_WIRE_SVC_DC` 관련 인덱스 힌트 **미생성**
- TABLE ACCESS FULL 패턴만 감지하고, PK 인덱스 비효율 패턴은 놓침

## 원인 분석

### 1. TABLE ACCESS FULL이 아님
`zord_wire_svc_dc` 테이블은 `TABLE ACCESS BY LOCAL INDEX ROWID` + `INDEX RANGE SCAN (ZORD_WIRE_SVC_DC_PK)` 사용.
PK 인덱스를 쓰고 있지만 조인 컬럼(`svc_mgmt_num`)이 PK 선두 컬럼이 아니라 비효율적.

```
TABLE ACCESS (BY LOCAL INDEX ROWID) OF 'ZORD_WIRE_SVC_DC' (Cost=151)
  INDEX (RANGE SCAN) OF 'ZORD_WIRE_SVC_DC_PK' (Cost=148, Card=10)
  Predicate: "B"."CHLD_SVC_MGMT_NUM"="D"."SVC_MGMT_NUM"(+)
```

### 2. 튜닝 포인트 과다 (94개)
전체 94개 튜닝 포인트를 보내면 LLM이 CRITICAL(MERGE JOIN CARTESIAN)에만 집중.
WIRE_SVC_DC의 HIGH 포인트가 묻힘.

### 3. gpt-4o-mini의 DBA 추론 한계
"PK 인덱스 대신 `svc_mgmt_num` 인덱스가 더 selective하다"는 DBA 수준 판단 필요.
gpt-4o-mini로는 이 수준의 추론이 불가.

## 해결

### 정적 분석 개선 (코드 수정)
1. **PK 인덱스 고비용 RANGE SCAN 탐지** (`explain_plan_parser.py`)
   - `_PK_INDEX_HIGH_COST = 100` 임계값 추가
   - `_PK` 접미사 인덱스 + Cost≥100 → HIGH 튜닝 포인트 자동 생성
   - Predicate에서 조인 컬럼 자동 추출: `_extract_join_columns()`

2. **Explain Plan 크기 제한** (`sql_analyzer.py`)
   - 대형 Explain Plan(100+ steps): 고비용 step만 필터링 (Cost≥50, TABLE ACCESS, MERGE JOIN 등)
   - 튜닝 포인트: severity 순 정렬, 상위 20개만 LLM에 전달

3. **프롬프트 개선** (`sql_analyzer_error_focused.txt`, `sql_analyzer_heuristic.txt`)
   - "비효율적 인덱스 선택" 패턴 분석 지시 추가
   - `/*+ INDEX(alias (column)) */` 구체적 예시 추가

### 모델 변경
- **SQL Analyzer 기본 모델**: `gpt-4o-mini` → `gpt-4o`
- gpt-4o는 6개 이슈 탐지, `(chld_svc_mgmt_num, svc_mgmt_num)` 인덱스 힌트 제안 성공

## 검증 결과

| 모델 | 이슈 수 | WIRE_SVC_DC 인덱스 힌트 | CARTESIAN 탐지 | NVL 인덱스 억제 | 시간 |
|------|---------|------------------------|---------------|----------------|------|
| gpt-4o-mini | 4개 | ❌ | ❌ | ❌ | ~14초 |
| gpt-4o | 6개 | ✅ `(chld_svc_mgmt_num, svc_mgmt_num)` | ✅ CRITICAL | ✅ HIGH | ~26초 |

### gpt-4o 탐지 이슈 목록
1. **[CRITICAL] SQL-001**: MERGE JOIN CARTESIAN으로 인한 성능 저하
2. **[HIGH] SQL-002**: INDEX RANGE SCAN의 높은 Cost → `/*+ INDEX(alias (chld_svc_mgmt_num, svc_mgmt_num)) */`
3. **[HIGH] SQL-003**: WHERE 절에서 NVL 함수 사용으로 인덱스 억제
4. **[MEDIUM] SQL-004**: LIKE 절에서 선행 와일드카드 사용으로 인덱스 억제
5. **[MEDIUM] SQL-005**: 서브쿼리로 인한 성능 저하
6. **[MEDIUM] SQL-006**: OR 조건으로 인한 인덱스 미사용

## 관련 파일
- `mider/tools/utility/explain_plan_parser.py`: PK 인덱스 고비용 탐지, `_extract_join_columns()`
- `mider/agents/sql_analyzer.py`: `_format_explain_plan()` 필터링/정렬, 기본 모델 변경
- `mider/config/prompts/sql_analyzer_error_focused.txt`: Step 3 비효율 인덱스 지시 추가
- `mider/config/prompts/sql_analyzer_heuristic.txt`: Explain Plan 활용 지침 추가
