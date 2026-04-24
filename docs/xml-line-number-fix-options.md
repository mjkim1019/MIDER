# XML 라인 번호 0 오탐 — 해결 방안 비교

> **작성 배경**: WebSquare XML 파일 분석 결과에서 일부 이슈가 `source:0`으로 찍히는 문제.
> 예: `ZORDSB0100010.xml:0` — `dataList ID 비어있음`, `이벤트 핸들러 세미콜론 포함` 등.
> **상태**: 원인 분석 완료, 해결 방안 3가지 제시. 사용자는 **Option A (lxml)** 선호.
> 폐쇄망 적용 가능성 검증 후 결정 예정.

---

## 1. 문제 요약

| 증상 | 발생 위치 |
|------|-----------|
| `line_start: 0` 으로 찍힘 | 이벤트 핸들러(ev:on*) 관련 이슈 |
| `line_start: 0` 으로 찍힘 | dataList ID 비어있음 / 유효성 이슈 |
| `line_start: 0` 으로 찍힘 | XML 파싱 에러 관련 이슈 |
| 정확한 라인 | 중복 컴포넌트 ID 이슈 ✅ (이미 raw-text 재탐색으로 해결) |
| 정확한 라인 | 인라인 JS 코드 이슈 ✅ (js_line_to_xml_line 매핑) |

## 2. 근본 원인

### 2-1. stdlib `xml.etree.ElementTree`의 구조적 한계
[`mider/tools/static_analysis/xml_parser.py:111`](mider/tools/static_analysis/xml_parser.py) 의 `ET.fromstring(content)`은 Element 객체에 **`sourceline` 속성을 제공하지 않는다**. (lxml은 기본 제공)

### 2-2. 이벤트/dataList/parse_errors 에 line 필드 누락
[`xml_parser.py:_extract_events`](mider/tools/static_analysis/xml_parser.py) 가 반환하는 event dict:
```python
{
    "element_id": elem_id,
    "element_tag": local_tag,
    "event_type": local_attr,
    "handler": attr_value.strip(),
    "handler_functions": handler_functions,
    # ← line 필드가 없음
}
```
`_extract_data_lists`, parse_errors(문자열 리스트) 도 마찬가지.

### 2-3. 프롬프트 스키마가 "모르면 0" 관례를 학습시킴
[`mider/config/prompts/xml_analyzer.txt:86-91`](mider/config/prompts/xml_analyzer.txt):
```
"location": {{
    "file": "{file_path}",
    "line_start": 0,    ← 기본값 예시가 0
    "line_end": 0,
    ...
}}
```
→ LLM은 line 정보가 없는 이슈에 스키마 기본값 `0`을 그대로 출력.

---

## 3. 해결 방안 3가지

### Option A — `lxml` 도입 (사용자 선호) ⭐

**개념**: stdlib ET를 lxml로 교체. lxml의 Element는 `.sourceline` 속성을 기본 보유.

```python
from lxml import etree as ET

root = ET.fromstring(content.encode("utf-8"))
for elem in root.iter():
    line = elem.sourceline   # ← 자동으로 붙어있음
```

**수정 파일**
- [`mider/tools/static_analysis/xml_parser.py`](mider/tools/static_analysis/xml_parser.py) — ET import 교체, 각 `_extract_*` 함수에서 `elem.sourceline` 수집하여 dict에 추가
- [`mider/config/prompts/xml_analyzer.txt`](mider/config/prompts/xml_analyzer.txt) — 스키마 기본값 `0 → null`, "line 필드가 있으면 그 값을 사용하라" 지시문 추가
- [`mider/tools/utility/markdown_report_formatter.py`](mider/tools/utility/markdown_report_formatter.py) — `line=0/None` 일 때 `:0` 표기 생략

**수정 분량**: 작음 (각 파일 20~50줄 수준)

**의존성 추가**: `lxml` 패키지 (~4MB wheel)

**PyInstaller 번들 크기 영향**: 현재 176MB → 예상 186~191MB (+10~15MB)

#### 🔥 폐쇄망 검증 체크리스트 (Option A 적용 전 필수)

**전제**: 빌드는 인터넷 가능한 개발 PC에서 PyInstaller로 수행하고, 폐쇄망에는 완성된 exe만 반입한다.
따라서 폐쇄망 머신에 lxml/Python을 설치할 필요가 없으며, 검증 초점은 **"번들된 exe가 깨끗한 머신에서 정상 동작하는가"** 에 있다.

##### A-1. PyInstaller 번들 호환성 (가장 중요)
- [ ] `pyinstaller mider.spec` 실행 시 lxml 관련 hidden import 경고 여부 확인
- [ ] [`mider.spec`](mider.spec) 의 `hiddenimports` 에 `lxml.etree`, `lxml._elementpath` 추가 필요 여부 확인 (PyInstaller가 자동 탐지하지만 누락될 수 있음)
- [ ] 번들된 exe 크기 측정 (목표: 200MB 이하)
- [ ] 번들 후 mider.exe를 **lxml이 설치되지 않은** 깨끗한 Windows 머신(가능하면 폐쇄망과 동일 Windows 버전)에 복사해서 실행 — `libxml2`/`libxslt` DLL이 exe 내부에 제대로 포함되었는지 검증

##### A-2. 런타임 동작 검증
- [ ] 기존 XML 파일(작은 샘플)로 분석 정상 동작 확인
- [ ] `sourceline` 속성이 실제로 값을 반환하는지 확인 (일부 빌드에서 누락 가능)
- [ ] WebSquare 네임스페이스(`xmlns:w2="..."`) 처리가 stdlib ET와 동일한지 확인
- [ ] DOCTYPE/ENTITY 방어 로직이 lxml에서도 동작하는지 확인
- [ ] `ET.ParseError` → `lxml.etree.XMLSyntaxError` 로 예외 타입이 바뀜 — except 절 수정 필요

##### A-3. 보안 고려사항 (lxml 특유)
lxml은 기본 설정에서 external entity 해석을 허용할 수 있어 **XXE 취약점** 우려가 있음. stdlib ET는 기본 비활성화.

대응:
```python
parser = ET.XMLParser(
    resolve_entities=False,   # external entity 해석 차단
    no_network=True,          # 네트워크 접근 차단
    huge_tree=False,          # Billion Laughs 방지
)
root = ET.fromstring(content.encode("utf-8"), parser)
```

##### A-4. 빌드 머신 설치 (간단)
- [ ] 개발 PC에서 `pip install lxml>=5.0` 정상 설치
- [ ] `python -c "from lxml import etree; e = etree.fromstring(b'<a><b/></a>'); print(e[0].sourceline)"` 으로 sourceline 동작 확인

> 폐쇄망 빌드 서버에 wheel을 따로 반입할 필요 없음. 빌드 산출물(exe)만 반입하면 됨.

##### A-5. 배포 롤백 계획
- [ ] 기존 stdlib ET 기반 코드를 git tag로 보존 (예: `v1.0.2-pre-lxml`)
- [ ] lxml 도입 후 문제 발생 시 해당 tag로 즉시 롤백 가능한지 확인

---

### Option B — stdlib `iterparse` 기반 재작성

**개념**: `ET.iterparse(StringIO(content), events=("start",))` 로 파싱 이벤트마다 `parser.CurrentLineNumber` 수집.

```python
from io import StringIO
it = ET.iterparse(StringIO(content), events=("start",))
line_map: dict[int, int] = {}
for event, elem in it:
    line_map[id(elem)] = it.parser.CurrentLineNumber
```

**수정 파일**: [`xml_parser.py`](mider/tools/static_analysis/xml_parser.py) 1개

**수정 분량**: 큼 — 파싱 전략 자체를 `fromstring` → `iterparse`로 바꾸면서 기존 `root.iter()` 기반 함수들 재구성 필요

**의존성 추가**: 없음

**장단점**
- ✅ stdlib만 사용, 폐쇄망 영향 없음
- ❌ `id(elem)` 기반 매핑은 GC 타이밍 주의 필요
- ❌ namespace 처리가 좀 더 복잡
- ❌ 재작성 리스크

---

### Option C — Raw-text 재탐색 확장 + 프롬프트 스키마 정비

**개념**: 이미 중복 ID에서 잘 동작 중인 `_find_id_lines` 패턴을 이벤트/dataList로 확장.

```python
def _find_event_line(lines, elem_id, attr_value):
    probe = attr_value.strip()[:40]
    id_pat = re.compile(rf'\bid=["\']{re.escape(elem_id)}["\']')
    for i, line in enumerate(lines, 1):
        if elem_id and id_pat.search(line) and probe in line:
            return i
    for i, line in enumerate(lines, 1):
        if probe and probe in line:
            return i
    return None
```

추가로 프롬프트 스키마의 `line_start: 0` 기본값을 `null`로 변경.

**수정 파일**
- [`xml_parser.py`](mider/tools/static_analysis/xml_parser.py)
- [`xml_analyzer.txt`](mider/config/prompts/xml_analyzer.txt)
- [`xml_analyzer.py`](mider/agents/xml_analyzer.py)
- [`markdown_report_formatter.py`](mider/tools/utility/markdown_report_formatter.py)

**수정 분량**: 각 파일 소폭, 총 4개 파일

**의존성 추가**: 없음

**장단점**
- ✅ 외부 의존 없음, 폐쇄망 안전
- ✅ 중복 ID에서 검증된 패턴 재사용 → 리스크 낮음
- ✅ 부분 적용 가능 (events → dataList 순)
- ⚠️ 멀티라인 속성 처리 제한 (실무상 거의 영향 없음)
- ⚠️ 파싱+텍스트스캔 이중 경로

---

## 4. 비교표

| 기준 | Option A (lxml) | Option B (iterparse) | Option C (raw-text) |
|------|----------------|----------------------|---------------------|
| 라인 정확도 | ★★★★★ 완벽 | ★★★★★ 완벽 | ★★★★ 시작 줄 정확 |
| 수정 파일 수 | 3 | 1 | 4 |
| 코드 수정량 | 소 | 대 (재작성) | 중 |
| 외부 의존성 | **lxml 추가** | 없음 | 없음 |
| 번들 크기 증가 | +10~15MB | 0 | 0 |
| 폐쇄망 친화도 | ⚠️ 검증 필요 | ✅ 안전 | ✅ 안전 |
| 구현 리스크 | 낮음 | 중간 | 낮음 |
| 장기 확장성 | ★★★★★ | ★★★★ | ★★★ |

---

## 5. 결정 가이드

### Option A를 선택해도 좋은 경우
- 폐쇄망 검증 체크리스트(§A-1 ~ §A-5) 모두 통과
- 향후 XML 분석 기능 확장 계획 있음 (XPath, XSLT 등)
- 번들 크기 증가(+15MB)가 수용 가능

### Option C로 후퇴해야 하는 경우
- PyInstaller 번들에 lxml DLL이 제대로 포함되지 않음 (A-1 실패)
- 깨끗한 머신에서 sourceline 미동작 또는 XXE 방어 설정 적용 난항 (A-2/A-3 실패)
- 번들 크기 증가가 현장 배포 제약에 걸림

### Option B를 고려하는 경우
- lxml 불가 + 장기적으로 완벽한 라인 정확도가 필요
- 재작성 작업량을 감수할 여유 있음

---

## 6. 권장 실행 순서 (Option A 확정 시)

1. **번들 호환성 검증** (§3. Option A의 A-1 ~ A-5)
   - 개발 PC에 `pip install lxml>=5.0` 후 작은 샘플로 테스트 빌드 1회 실행
   - 번들된 exe를 lxml 미설치 깨끗한 머신에 복사하여 sourceline 동작 확인

2. **검증 통과 시 본 적용**
   - [`pyproject.toml`](pyproject.toml) dependencies 에 `lxml>=5.0` 추가
   - [`mider/tools/static_analysis/xml_parser.py`](mider/tools/static_analysis/xml_parser.py) 수정:
     - import 교체
     - `ET.XMLParser(resolve_entities=False, no_network=True)` 안전 설정
     - `_extract_events`, `_extract_data_lists`, parse_errors 에 `sourceline` 추가
   - [`mider/config/prompts/xml_analyzer.txt`](mider/config/prompts/xml_analyzer.txt) 수정:
     - 스키마 기본값 `0 → null`
     - 위치 표기 지시문 추가
   - [`mider/tools/utility/markdown_report_formatter.py`](mider/tools/utility/markdown_report_formatter.py) 수정:
     - `line=0/None` 시 `:0` 표기 생략
   - [`mider.spec`](mider.spec) hiddenimports 확인

3. **검증 실패 시 Option C 롤백**
   - 기존 stdlib ET 유지
   - raw-text 재탐색 확장만 적용
   - 프롬프트 스키마 정비는 Option A/C 공통이므로 선행 적용 가능

---

## 7. 참고: 이미 작동 중인 유사 패턴

| 케이스 | 처리 방식 | 위치 |
|--------|----------|------|
| 중복 ID 라인 | raw-text 재탐색 (`_find_id_lines`) | [xml_parser.py:356](mider/tools/static_analysis/xml_parser.py) |
| 인라인 JS 라인 | offset_map 역매핑 (`js_line_to_xml_line`) | [xml_parser.py:56](mider/tools/static_analysis/xml_parser.py) |

중복 ID 케이스는 Option C의 raw-text 재탐색이 실전에서 안정적으로 동작함을 보여주는 근거. Option A 미적용 시에도 동일 패턴 확장으로 이벤트/dataList 문제를 해결 가능.
