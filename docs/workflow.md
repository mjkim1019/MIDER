# Mider Workflow Diagrams

## 1. 전체 파이프라인 (Overall Pipeline)

```mermaid
flowchart TB
    subgraph CLI["CLI (main.py)"]
        A["--files *.js *.c *.sql\n--explain-plan plan.txt\n--output ./output"]
    end

    A --> B["OrchestratorAgent"]

    subgraph Phase0["Phase 0: Task Classification"]
        B --> C["파일 경로 검증\n+ glob 확장"]
        C --> D["TaskClassifierAgent"]
        D --> E["ExecutionPlan\n(sub_tasks, dependencies)"]
    end

    subgraph Phase1["Phase 1: Context Collection"]
        E --> F["ContextCollectorAgent"]
        F --> G["FileContext\n(imports, calls, patterns)"]
    end

    subgraph Phase2["Phase 2: Code Analysis"]
        G --> H{"언어별 라우팅"}
        H -->|javascript| I["JavaScriptAnalyzerAgent"]
        H -->|c| J["CAnalyzerAgent\n(3경로 분기)"]
        H -->|proc| K["ProCAnalyzerAgent"]
        H -->|sql| L["SQLAnalyzerAgent\n(+Explain Plan)"]
        H -->|xml| M["XMLAnalyzerAgent\n(+JS 핸들러 검증)"]
        I --> N["AnalysisResult[]"]
        J --> N
        K --> N
        L --> N
        M --> N
    end

    subgraph Phase3["Phase 3: Report Generation"]
        N --> O["ReporterAgent"]
        O --> P["issue-list.json"]
        O --> Q["checklist.json"]
        O --> R["summary.json"]
        O --> S["deployment-checklist.json"]
    end

    P --> T{"Critical\nissues?"}
    T -->|Yes| U["Exit 1\n배포 차단"]
    T -->|No| V["Exit 0\n배포 가능"]

    style Phase0 fill:#e3f2fd,stroke:#1565c0
    style Phase1 fill:#e8f5e9,stroke:#2e7d32
    style Phase2 fill:#fff3e0,stroke:#e65100
    style Phase3 fill:#fce4ec,stroke:#c62828
```

## 2. CAnalyzerAgent 워크플로우 (3경로 분기)

```mermaid
flowchart TB
    A["C 파일 입력"] --> B["CHeuristicScanner\n(regex 6종 패턴, 비용 0)"]
    B --> C{"clang-tidy\n사용 가능?"}

    C -->|Yes| D["ClangTidyRunner 실행"]
    D --> E["_merge_warnings\n(clang-tidy + heuristic\n중복 제거)"]
    E --> F["경로 A: Error-Focused"]
    F --> G["구조 요약 + 에러 함수 추출"]
    G --> H["gpt-4o 호출\n(c_analyzer_error_focused)"]

    C -->|No| I{">500줄?"}

    I -->|Yes| J["경로 B: 2-Pass"]
    J --> K["Pass 1: 위험 함수 선별"]
    K --> K1["함수별 패턴 요약 생성"]
    K1 --> K2["gpt-4o-mini 호출\n(c_prescan_fewshot)"]
    K2 --> K3{"risky_functions\n있음?"}
    K3 -->|Yes| L["Pass 2: 함수별 개별 분석"]
    L --> L1["함수 코드 + warnings 추출"]
    L1 --> L2["gpt-4o 개별 호출\n(최대 3개 병렬)"]
    L2 --> L3["이슈 합산 + 재번호\n(C-001, C-002...)"]
    K3 -->|No| M

    I -->|No| M["경로 C: Heuristic"]
    M --> M1["파일 최적화\n(전체 코드)"]
    M1 --> M2["gpt-4o 호출\n(c_analyzer_heuristic)"]

    H --> N["AnalysisResult"]
    L3 --> N
    M2 --> N

    style F fill:#ffcdd2,stroke:#c62828
    style J fill:#fff9c4,stroke:#f9a825
    style M fill:#c8e6c9,stroke:#2e7d32
```

## 3. SQLAnalyzerAgent 워크플로우 (Explain Plan + 정적 이슈)

```mermaid
flowchart TB
    A["SQL 파일 입력"] --> B["Step 1: SQLSyntaxChecker\n(sqlparse 문법 검증)"]
    B --> C["Step 2: AstGrepSearch\n(5종 패턴: select_star,\nfunction_in_where, like_wildcard,\nsubquery, or_condition)"]

    C --> D{"--explain-plan\n파일 있음?"}
    D -->|Yes| E["Step 3: ExplainPlanParser"]
    E --> E1["steps[] 추출\n(Operation, Cost, Rows)"]
    E1 --> E2["tuning_points[] 탐지"]
    D -->|No| F["explain_plan_data = None"]

    E2 --> G{"syntax_errors OR\nstatic_patterns?"}
    F --> G

    G -->|Yes| H["경로 A: Error-Focused\n(sql_analyzer_error_focused)"]
    G -->|No| I["경로 B: Heuristic\n(sql_analyzer_heuristic)"]

    H --> J["Step 5: gpt-4o 호출"]
    I --> J

    J --> K["LLM Issues"]

    E2 --> L["Step 4: _generate_static_issues"]
    L --> L1["CARTESIAN JOIN → critical"]
    L --> L2["PK INDEX Cost>100 → high"]
    L --> L3["TABLE ACCESS FULL → high"]
    L1 --> M["Static Issues"]
    L2 --> M
    L3 --> M

    K --> N["Step 6: _merge_issues\n(LLM + Static 병합,\n중복 제거)"]
    M --> N

    N --> O["최종 issues[]\n(SQL-001, SQL-002...)\nsource: hybrid"]

    style E fill:#e3f2fd,stroke:#1565c0
    style L fill:#fff3e0,stroke:#e65100
    style N fill:#e8f5e9,stroke:#2e7d32
```

## 4. XMLAnalyzerAgent 워크플로우

```mermaid
flowchart TB
    A["XML 파일 입력\n(WebSquare/Proframe)"] --> B["XXE 방어 검사\n(DOCTYPE/ENTITY 거부)"]
    B --> C["XMLParser\n(ElementTree 파싱)"]

    C --> D["data_lists[]\n(dataList + columns)"]
    C --> E["events[]\n(ev:onclick, handler_functions)"]
    C --> F["component_ids[]\n+ duplicate_ids[]"]
    C --> G["parse_errors[]"]

    E --> H["JS 핸들러 검증\n_validate_js_handlers"]
    H --> H1["매칭 JS 파일 탐색\n(file.js 또는 file_wq.js)"]
    H1 --> H2["handler_functions →\nJS 파일에서 grep"]
    H2 --> I["missing_handlers[]"]

    G --> J{"parse_errors OR\nduplicate_ids OR\nmissing_handlers?"}
    F --> J
    I --> J

    J -->|Yes| K["경로 A: Error-Focused\n(xml_analyzer_error_focused)"]
    J -->|No| L["경로 B: Heuristic\n(xml_analyzer_heuristic)"]

    K --> M["gpt-4o-mini 호출\n(fallback: gpt-4o)"]
    L --> M

    M --> N["AnalysisResult"]

    style B fill:#ffcdd2,stroke:#c62828
    style H fill:#e3f2fd,stroke:#1565c0
    style K fill:#ffcdd2,stroke:#c62828
    style L fill:#c8e6c9,stroke:#2e7d32
```

## 5. ReporterAgent 워크플로우 (4개 출력)

```mermaid
flowchart TB
    A["Phase 2\nAnalysisResult[]"] --> B["ReporterAgent"]

    B --> C["Step 1: 이슈 수집 + 심각도 정렬"]
    C --> D["IssueList 생성"]
    D --> D1["issue-list.json\n(전체 이슈 목록)"]

    C --> E["Step 2: ChecklistGenerator"]
    E --> E1["checklist.json\n(검증 명령어 체크리스트)"]

    C --> F["Step 3: Summary 생성"]
    F --> F1["리스크 판정"]
    F1 --> F2{"Critical > 0?"}
    F2 -->|Yes| F3["CRITICAL\n배포 차단"]
    F2 -->|No| F4{"High >= 3?"}
    F4 -->|Yes| F5["HIGH\n배포 차단"]
    F4 -->|No| F6{"High >= 1?"}
    F6 -->|Yes| F7["MEDIUM\n배포 가능"]
    F6 -->|No| F8["LOW\n배포 가능"]
    F3 --> F9["LLM 리스크 설명 생성\n(gpt-4o-mini)"]
    F5 --> F9
    F7 --> F9
    F8 --> F9
    F9 --> F10["summary.json"]

    C --> G["Step 4: DeploymentChecklistGenerator"]
    G --> G1["파일 → 섹션 매핑"]
    G1 --> G2[".js/.xml → 화면"]
    G1 --> G3[".c (TP) → TP"]
    G1 --> G4[".c (기타) → 모듈"]
    G1 --> G5[".pc → 배치"]
    G1 --> G6[".sql → DBIO"]
    G2 --> G7["deployment-checklist.json\n(5개 섹션)"]
    G3 --> G7
    G4 --> G7
    G5 --> G7
    G6 --> G7

    style D1 fill:#e3f2fd,stroke:#1565c0
    style E1 fill:#e8f5e9,stroke:#2e7d32
    style F10 fill:#fff3e0,stroke:#e65100
    style G7 fill:#fce4ec,stroke:#c62828
```

## 6. 토큰 최적화 전략

```mermaid
flowchart LR
    subgraph Before["최적화 전"]
        A["전체 파일 코드\n(수천 줄)"]
    end

    subgraph After["최적화 후 (~80% 절감)"]
        B["구조 요약\n(imports, 함수 시그니처,\n전역 변수)"]
        C["에러 함수만 추출\n(에러 라인 포함 함수\n전체 코드)"]
        D["파일 최적화\n(≤500줄: 전체\n>500줄: head+tail)"]
    end

    A -->|"Error-Focused\n경로"| B
    A -->|"Error-Focused\n경로"| C
    A -->|"Heuristic\n경로"| D

    style Before fill:#ffcdd2,stroke:#c62828
    style After fill:#c8e6c9,stroke:#2e7d32
```

## 7. 데이터 흐름 요약

```mermaid
flowchart LR
    CLI["CLI\n--files\n--explain-plan"] --> Orch["Orchestrator"]

    Orch --> TC["TaskClassifier"]
    TC -->|ExecutionPlan| CC["ContextCollector"]
    CC -->|FileContext| Analyzers

    subgraph Analyzers["Phase 2 Analyzers"]
        JS["JS Analyzer"]
        C["C Analyzer"]
        PC["ProC Analyzer"]
        SQL["SQL Analyzer"]
        XML["XML Analyzer"]
    end

    CLI -.->|explain_plan_file| SQL

    Analyzers -->|"AnalysisResult[]"| Reporter["Reporter"]

    Reporter --> Out1["issue-list.json"]
    Reporter --> Out2["checklist.json"]
    Reporter --> Out3["summary.json"]
    Reporter --> Out4["deployment-checklist.json"]

    subgraph Tools["정적 분석 도구"]
        T1["ESLint"]
        T2["clang-tidy"]
        T3["proc"]
        T4["sqlparse"]
        T5["XMLParser"]
        T6["CHeuristicScanner"]
        T7["ExplainPlanParser"]
    end

    T1 -.-> JS
    T2 -.-> C
    T6 -.-> C
    T3 -.-> PC
    T4 -.-> SQL
    T7 -.-> SQL
    T5 -.-> XML
```
