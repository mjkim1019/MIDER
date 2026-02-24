# 챕터 5: 보안 체크 매뉴얼

---

## 5.1 코드 작성 시 보안 체크리스트

이 파일에 관련 코드 패턴이 감지되면 아래 체크리스트를 점검하세요.

## 5.2 메모리 안전성 (C)

| 위험 패턴 | 안전한 대안 |
|-----------|-----------|
| `strcpy(dest, src)` | `strncpy(dest, src, sizeof(dest)-1)` |
| `sprintf(buf, fmt, ...)` | `snprintf(buf, sizeof(buf), fmt, ...)` |
| `gets(buf)` | `fgets(buf, sizeof(buf), stdin)` |
| `malloc()` 후 NULL 미체크 | `if (ptr == NULL) return;` |
| `free()` 후 재사용 | `ptr = NULL;` 추가 |

### 점검 항목
- [ ] 모든 malloc 반환값에 NULL 체크가 있는가?
- [ ] 모든 malloc에 대응하는 free가 있는가?
- [ ] 버퍼 크기를 하드코딩하지 않고 sizeof()를 사용했는가?
- [ ] free 후 포인터를 NULL로 설정했는가?

## 5.3 XSS / Injection (JavaScript)

| 위험 패턴 | 안전한 대안 |
|-----------|-----------|
| `innerHTML = userInput` | `textContent = userInput` |
| `eval(code)` | 사용 금지 |
| `document.write()` | DOM API 사용 |
| `setTimeout(string)` | `setTimeout(function)` |

### 점검 항목
- [ ] 사용자 입력이 innerHTML에 직접 들어가지 않는가?
- [ ] eval() 사용이 없는가?
- [ ] 외부 입력에 대한 sanitize가 있는가?

## 5.4 데이터 무결성 (Pro*C)

| 위험 패턴 | 안전한 대안 |
|-----------|-----------|
| EXEC SQL 후 SQLCA 미체크 | `if (sqlca.sqlcode != 0)` 추가 |
| NULL 컬럼에 INDICATOR 미사용 | `:indicator` 변수 추가 |
| CURSOR OPEN 후 CLOSE 누락 | 예외 경로에도 CLOSE 보장 |
| 예외 시 ROLLBACK 누락 | 에러 핸들러에 ROLLBACK 추가 |

### 점검 항목
- [ ] 모든 EXEC SQL 뒤에 SQLCA 체크가 있는가?
- [ ] NULL 가능 컬럼에 INDICATOR 변수가 있는가?
- [ ] CURSOR OPEN과 CLOSE가 짝을 이루는가?
- [ ] 에러 경로에서 ROLLBACK이 호출되는가?

## 5.5 API 키 / 비밀 정보

- .env 파일에 저장, 코드에 하드코딩 금지
- `os.environ["MIDER_API_KEY"]`로 접근
- .gitignore에 .env 포함 확인
- 로그에 API 키가 출력되지 않는지 확인
