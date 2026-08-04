# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

이 저장소는 로컬 컴퓨터에 설치된 **Hermes**가 필요할 때 불러 쓸 수 있는
자동화 Python 스크립트("스킬")들을 만들고 모아두는 공간입니다.
새로운 자동화를 추가하거나 기존 스킬을 수정할 때는 이 문서의 원칙을 따릅니다.

## 1. 자주 사용하는 명령어

각 스킬은 독립적인 폴더로, 공통 빌드 시스템이 없고 스킬별로 의존성/실행/테스트를 따로 다룹니다.

```bash
# 최초 1회: 공용 환경변수 설정
cp .env.example .env   # 이후 .env에 실제 키 값 입력 (docs/ENVIRONMENT.md 참고)

# 특정 스킬 의존성 설치
pip install -r skills/<skill_name>/requirements.txt

# 특정 스킬 실행
python skills/<skill_name>/src/main.py

# 특정 스킬 테스트 실행 (pytest 필요: pip install pytest)
pytest skills/<skill_name>/tests/

# 단일 테스트 함수만 실행
pytest skills/<skill_name>/tests/test_main.py::test_run_default

# 여러 스킬이 공유하는 SQLite DB 내용 확인 (예: data/marathon.db)
sqlite3 data/<db_name>.db ".tables"
```

## 2. 폴더 구조

```
Automation_Script/
├── CLAUDE.md                 # 이 문서 (프로젝트 운영 원칙)
├── README.md                 # 저장소 소개, 스킬 목록 개요
├── .env.example               # 공용 환경변수 템플릿 (.env는 커밋하지 않음)
├── .gitignore
├── docs/
│   ├── ENVIRONMENT.md        # 공용 .env 변수 목록/설명 (Claude/OpenAI/Telegram/Slack 등)
│   └── templates/
│       ├── PRD_template.md   # 신규 스킬용 PRD 템플릿
│       └── SRS_template.md   # 신규 스킬용 SRS 템플릿
├── shared/                   # 여러 스킬이 공통으로 쓰는 유틸 모듈(코드)
├── data/                     # 여러 스킬이 공통으로 쓰는 데이터(DB 파일 등). 실제 파일은 커밋하지 않음
└── skills/
    └── <skill_name>/         # 스킬(자동화 스크립트) 1개 = 폴더 1개
        ├── docs/
        │   └── PRD.md 또는 SRS.md   # 이 스킬의 요구사항 문서 (필수, 최초 산출물)
        ├── src/
        │   └── main.py        # 실행 진입점
        ├── tests/
        │   └── test_main.py
        ├── requirements.txt   # 이 스킬만의 의존성 (스킬 간 의존성 격리)
        ├── skill.yaml          # Hermes 등록용 메타데이터
        ├── README.md           # 사용법, 입출력, 실행 예시
        └── CHANGELOG.md        # 버전별 변경 이력
```

- 스킬 1개 = 폴더 1개. 서로 다른 스킬은 서로의 코드를 직접 import하지 않습니다.
  공통 로직이 필요하면 `shared/`로 올려 양쪽에서 참조합니다.
- 폴더/파일명은 스네이크 케이스(`snake_case`)를 사용합니다. (예: `pdf_merge`, `slack_daily_report`)
- 신규 스킬을 만들 때는 [`skills/_example_skill/`](skills/_example_skill/)의 폴더 구조를 그대로 복사해서 시작합니다.
- 여러 스킬이 같은 DB/데이터 파일을 공유해야 하는 경우(예: 검색 스킬이 채워넣은 DB를 다른
  관리용 스킬이 읽고 쓰는 경우), 그 데이터 파일은 특정 스킬 폴더 안이 아니라 저장소 최상위
  `data/`에 둡니다 (예: `data/marathon.db`). 이렇게 하면 두 스킬이 서로의 코드를 import하지
  않고도 같은 경로만 바라보면 되어 3.3의 스킬 격리 원칙과 충돌하지 않습니다. 특정 스킬 하나만
  쓰는 데이터(캐시, 임시 파일 등)는 계속 해당 스킬 폴더 안에 둡니다. `data/` 안의 실제 파일은
  생성되는 데이터이므로 커밋하지 않습니다 (`.gitignore` 참고).

## 3. 개발 원칙

### 3.1 PRD/SRS 문서 선(先) 작성
모든 스킬 개발은 코드보다 문서가 먼저입니다.
- 간단한 자동화(개인용, 입출력이 명확한 스크립트) → `docs/templates/PRD_template.md` 사용
- 복잡한 자동화(외부 API 연동, 여러 단계 처리, 상태 관리 필요) → `docs/templates/SRS_template.md` 사용
- 문서에는 최소한 **목적, 입력/출력, 트리거 조건(언제 실행되는가), 성공/실패 기준, 제약사항**을 포함합니다.
- PRD/SRS가 없는 스킬 폴더는 미완성 상태로 간주하고 `skills/` 최상위에 커밋하지 않습니다.
- 문서 없이 코드부터 작성해야 할 만큼 급한 경우에도, 코드 작성 직후 문서를 채워 넣고 별도 커밋으로 남깁니다.

### 3.2 단계별 Git 커밋
- 작업은 큰 단위로 몰아서 커밋하지 않고, 의미 있는 단계마다 커밋합니다.
  예: `1) PRD 작성` → `2) 기본 골격/의존성 구성` → `3) 핵심 로직 구현` → `4) 테스트 추가` → `5) skill.yaml 등록` → `6) 문서 보완`
- 커밋 메시지는 `<스킬명>: <내용>` 형식을 권장합니다. (예: `pdf_merge: PRD 초안 작성`)
- 한 커밋에는 하나의 논리적 변경만 담습니다 (여러 스킬을 한 커밋에 섞지 않기).
- 비밀정보(API 키, 토큰, 개인정보)가 포함된 파일은 절대 커밋하지 않습니다. (3.6 참고)

### 3.3 스킬 단위 격리
- 각 스킬은 자체 `requirements.txt`를 가지며, 필요하면 자체 가상환경(venv)을 사용합니다.
- 다른 스킬 폴더의 파일을 직접 참조하지 않습니다. 공통 기능은 `shared/`에 둡니다.
- 하나의 스킬을 삭제/이동해도 다른 스킬이 깨지지 않아야 합니다.

### 3.4 코드 품질 최소 기준
- 함수/클래스에는 타입 힌트를 사용합니다.
- `print` 대신 표준 `logging` 모듈을 사용해 실행 로그를 남깁니다 (Hermes가 실행 결과를 추적할 수 있도록).
- 예외는 삼키지 않고 명확히 처리하거나 상위로 전달합니다. 실패 시 원인을 알 수 있는 메시지를 남깁니다.
- 스크립트는 단독 실행(`python src/main.py`)이 가능해야 하며, 실행에 필요한 인자/환경변수는 README에 명시합니다.

### 3.5 테스트
- 핵심 로직에는 최소한의 스모크 테스트(정상 케이스 1개 이상)를 `tests/`에 작성합니다.
- 외부 API를 호출하는 스킬은 실제 호출 대신 mock을 사용한 테스트를 기본으로 하고,
  실제 연동 확인은 수동 실행으로 별도 검증합니다.

### 3.6 보안 및 비밀정보 관리
- API 키, 비밀번호, 토큰은 코드에 하드코딩하지 않고 `.env` 또는 환경변수로 관리합니다.
- 여러 스킬이 공통으로 쓰는 비밀정보(Claude/OpenAI API Key, Telegram Bot Token, Slack Token 등)는
  저장소 루트의 `.env` 파일 하나로 관리하며, 실제 값이 없는 템플릿 `.env.example`만 커밋합니다.
  변수명과 용도는 [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md)에 표로 정리되어 있으며,
  새 공용 변수를 추가할 때는 이 문서와 `.env.example`을 함께 갱신합니다.
- 특정 스킬 하나에서만 쓰는 값은 공용 `.env`에 넣지 않고 해당 스킬의 문서에 기록합니다.
- `.env`, 인증 파일, 개인정보가 담긴 샘플 데이터는 `.gitignore`에 등록합니다.
- 커밋 전에는 항상 diff를 확인해 비밀정보가 섞이지 않았는지 점검합니다.

### 3.7 Hermes 스킬 등록 (`skill.yaml`)
각 스킬 폴더의 `skill.yaml`에 아래 정보를 최소한으로 채워 Hermes가 인식할 수 있게 합니다.
```yaml
name: <skill_name>
description: <이 스킬이 무엇을 하는지 한 줄 요약>
entrypoint: src/main.py
trigger_keywords: [<Hermes가 이 스킬을 골라야 할 때 참고할 키워드>]
requirements: requirements.txt
version: 0.1.0
```
> 참고: 위 필드는 초안이며, 실제 Hermes의 스킬 매니페스트 스펙이 확인되면
> (필드명, 필수/선택 여부 등) 이 섹션과 템플릿을 그 스펙에 맞게 갱신합니다.

### 3.8 문서화
- 스킬별 `README.md`에는 목적, 실행 방법, 입출력 예시, 의존성 설치 방법을 적습니다.
- 동작에 영향을 주는 변경(입출력 변경, 옵션 추가/제거 등)은 `CHANGELOG.md`에 버전과 함께 기록합니다.
- 저장소 최상위 `README.md`에는 현재 존재하는 스킬 목록과 한 줄 설명을 유지합니다.

### 3.9 버전 관리
- `skill.yaml`의 `version`은 [Semantic Versioning](https://semver.org/lang/ko/) (`MAJOR.MINOR.PATCH`)을 따릅니다.
- 입출력 형식이 바뀌는 등 기존 사용 방식과 호환되지 않는 변경은 MAJOR를 올립니다.

## 4. 신규 스킬 추가 워크플로우 (체크리스트)
1. `skills/<skill_name>/` 폴더 생성
2. `docs/templates/`에서 PRD 또는 SRS 템플릿 복사 → `docs/PRD.md`(또는 `SRS.md`) 작성 → 커밋
3. `src/`, `requirements.txt`, 기본 골격 작성 → 커밋
4. 핵심 로직 구현 → 커밋
5. `tests/`에 최소 테스트 작성 → 커밋
6. `skill.yaml` 작성해 Hermes 등록 준비 → 커밋
7. `README.md`, `CHANGELOG.md` 작성/갱신 → 커밋
8. 저장소 최상위 `README.md`의 스킬 목록 갱신 → 커밋

## 5. 템플릿 위치
- PRD: [`docs/templates/PRD_template.md`](docs/templates/PRD_template.md)
- SRS: [`docs/templates/SRS_template.md`](docs/templates/SRS_template.md)
- 예시 스킬 골격: [`skills/_example_skill/`](skills/_example_skill/)
