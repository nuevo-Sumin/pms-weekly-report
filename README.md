# 📋 PMS 주간보고 자동화 (PMS Weekly Report Automation)

매주 금요일 로컬에서 실행 중인 Chrome 브라우저 세션(CDP: `http://localhost:9222`)에 연결하여, Redmine 내 작업 일감 목록(`REDMINE_MY_ISSUES_URL`)에서 CSV를 다운로드받고, 대화형 CLI 입력 및 필터링을 거쳐 주간보고 텍스트를 생성한 뒤, **PMS 주간보고 등록 화면(`https://pms.metabuild.co.kr/mypage/wkly/save`)에 진척률 100 및 금주 실적/차주 계획을 자동 입력하고 저장**하는 자동화 프로젝트입니다.

---

## 📁 디렉토리 구조

```text
pms-weekly-report/
├── README.md               # 본 문서 (PMS 주간보고 자동화 가이드)
├── run_pms_sync.py         # Python 스크립트 소스코드 (동적 경로/권한 대응)
├── build_exe.bat           # PyInstaller 원클릭 단일 exe 빌드 배치파일
├── start_chrome_debug.bat  # Chrome 원격 디버깅(Redmine/PMS 탭 자동오픈) 실행기
├── Untitled-1.html         # 과거 완료기한 제외 규칙이 적용된 웹 브라우저용 생성기
├── dist/                   # 빌드 결과물 폴더
│   └── PMS_Weekly_Report.exe  # [배포용] Python 미설치 PC에서도 단독 실행 가능한 단일 파일
└── downloads/              # 다운로드된 weekly_issues.csv 저장 폴더 (실행 위치 기준 동적 생성)
```

---

## 👥 팀원 배포 및 초간편 실행 안내 (단일 .exe 파일 배포)

이제 팀원들에게 **`PMS_Weekly_Report.exe` 단 하나의 파일만 전달**하면 모든 것이 자동으로 동작합니다! (Python 설치 및 별도 .bat 실행 불필요)

### 📦 배포 파일
- **`dist/PMS_Weekly_Report.exe`** 파일 하나만 복사하여 팀원에게 전달합니다.

### 🏃 실행 및 동작 순서
1. **`PMS_Weekly_Report.exe` 더블클릭 실행**:
   - 프로그램이 실행되면서 로컬에 디버깅 모드(포트 9222)의 Chrome이 켜져 있는지 자동 확인합니다.
   - 꺼져 있다면 **자동으로 Chrome 창을 실행**하고 두 개의 탭을 엽니다:
     - **1번째 탭**: Redmine 내 작업 일감 목록
     - **2번째 탭**: PMS 주간보고 목록
2. **콘솔에서 로그인 확인 대기**:
   - 콘솔 창에 다음과 같은 로그인 확인 프롬프트가 표시됩니다:
     ```text
     =================================================================
      [Chrome 브라우저 로그인 확인 안내]
     =================================================================
      Chrome 브라우저에 2개의 작업 탭이 열려 있어야 합니다:
        - 1번째 탭: Redmine 내 작업 일감 목록
        - 2번째 탭: PMS 주간보고 목록

      * 두 사이트 모두 로그인이 완료되어 있어야 정상 진행됩니다.
      * (한 번 로그인하면 로그인 세션이 chrome_profile에 안전하게 보존됩니다)
     =================================================================

     [?] 두 사이트 모두 로그인을 완료하셨습니까? (완료 후 Enter를 누르세요): 
     ```
3. **로그인 후 Enter 키 입력**:
   - 브라우저에서 로그인을 마친 후 콘솔 창에서 Enter를 누르면, 즉시 Redmine CSV 다운로드 ➔ 대화형 확인(사업관리/장기건) ➔ 보고서 미리보기 ➔ PMS 진척률 100 및 내용 입력/저장이 순차적으로 자동 완료됩니다!

---

## 🛠️ PyInstaller 빌드 방법 (.exe 생성)

스크립트를 수정한 후 다시 단일 `.exe` 실행 파일로 패키징할 때 사용합니다.

### 방법 1. 원클릭 빌드 배치 파일 실행 (가장 추천)
`pms-weekly-report/build_exe.bat`을 더블클릭하면 자동으로 의존성 확인 및 최신 `PMS_Weekly_Report.exe`를 빌드합니다.

### 방법 2. CLI 명령어로 직접 빌드
```powershell
cd pms-weekly-report
python -m PyInstaller --onefile --noconfirm --clean --name "PMS_Weekly_Report" --collect-all playwright run_pms_sync.py
```
> **주요 빌드 옵션 설명**:
> - `--onefile`: 모든 의존성을 단일 `.exe` 파일로 압축 패키징
> - `--collect-all playwright`: Playwright 실행에 필수적인 내부 드라이버(Node.js 런타임 등)를 exe 내부에 완전 번들링
> - `--clean`: 이전 빌드 캐시를 초기화하여 충돌 방지

---

## ⚙️ 사전 환경 준비 (Prerequisites)

### 1. Python 패키지 설치
```bash
pip install playwright
```

### 2. Chrome 원격 디버깅 모드 실행 (포트: 9222)
기존에 열려 있는 개인 Chrome 창과 간섭되지 않도록 별도 프로필 폴더를 지정해 실행합니다.
프로젝트 루트(`../`)의 **`start_chrome_debug.bat`**을 더블클릭하거나 아래 명령어를 실행합니다:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="D:\skim6220\redmine-weekly-notice\chrome_profile" "https://support.kyci.or.kr"
```

### 3. 로그인 세션 확인
열린 디버깅 브라우저 창에서 다음 두 사이트에 **로그인된 상태**여야 합니다:
- **Redmine**: `https://support.kyci.or.kr`
- **PMS**: `https://pms.metabuild.co.kr`

---

## 🚀 실행 방법 (Usage)

### 1. 기본 전체 파이프라인 실행 (가장 추천)
Redmine CSV 다운로드 ➔ 대화형 확인(사업관리/장기건) ➔ 보고서 미리보기 ➔ PMS 자동 입력/저장을 순차 진행합니다.
```powershell
python pms-weekly-report/run_pms_sync.py
```

### 2. PMS 등록 없이 텍스트만 생성 (`--skip-pms`)
보고서 내용만 터미널에서 확인하거나 수기로 복사해서 사용하고 싶을 때 실행합니다.
```powershell
python pms-weekly-report/run_pms_sync.py --skip-pms
```

### 3. 이미 다운로드된 CSV 파일로 텍스트 생성 테스트 (`--csv`)
브라우저 조작 없이 로컬 CSV 파일을 직접 지정해 가공 로직만 테스트합니다.
```powershell
python pms-weekly-report/run_pms_sync.py --csv "./pms-weekly-report/downloads/weekly_issues.csv" --skip-pms
```

### 4. 기타 CLI 옵션
- `--date YYYY-MM-DD`: 기준 실행일자를 수동 지정 (기본값: 오늘 날짜 금요일 기준)
- `--auto-yes` 또는 `-y`: 대화형 확인 프롬프트를 기본값(사업관리 N, 장기건 제외)으로 자동 통과
- `--cdp-url`: Chrome 디버깅 포트 URL 변경 (기본값: `http://localhost:9222`)

---

## 🔍 세부 실행 단계 및 필터링 규칙

### [Step 1] Redmine CSV 다운로드
- 대상 URL: `https://support.kyci.or.kr/redmine/issues?utf8=%E2%9C%93&set_filter=1&sort=status%3Adesc%2Cid%3Adesc&f%5B%5D=status_id&op%5Bstatus_id%5D=%21&v%5Bstatus_id%5D%5B%5D=11&v%5Bstatus_id%5D%5B%5D=12&v%5Bstatus_id%5D%5B%5D=13&f%5B%5D=assigned_to_id&op%5Bassigned_to_id%5D=%3D&v%5Bassigned_to_id%5D%5B%5D=me&f%5B%5D=&c%5B%5D=project&c%5B%5D=tracker&c%5B%5D=status&c%5B%5D=priority&c%5B%5D=subject&c%5B%5D=assigned_to&c%5B%5D=updated_on&group_by=&t%5B%5D=` (내 작업 일감 목록)
- 우측 하단 `[내보내기] ➔ [CSV]`를 클릭하여 `./downloads/weekly_issues.csv`로 저장합니다.
- `cp949`, `euc-kr`, `utf-8-sig` 인코딩을 자동 감지하여 한글 깨짐을 원천 방지합니다.

### [Step 2] 데이터 가공 및 대화형 확인 (Untitled-1.html 기반)
1. **[대화형 확인 1] 사업관리 항목 선택**:
   - `y` 입력 시 아래 5개 항목 중 포함할 번호를 쉼표나 공백으로 입력:
     - `(1) 주간보고`
     - `(2) 휴가신청 관련 처리`
     - `(3) 인력 변동 관련 처리`
     - `(4) 안전망시스템공지사항 등록`
     - `(5) 월간보고`
   - 선택된 항목들은 `(1) 사업관리` 하위에 불릿(`   - `)으로 배치되고, 수행 섹션은 `(2) 수행`이 됩니다 (미포함 시 `(1) 수행` 단독).
2. **[대화형 확인 2] 3개월(90일) 이상 장기 미완료 건 수기 확인**:
   - 등록일 기준 90일 이상 경과한 '신규'/'진행중' 일감 목록을 콘솔에 출력합니다.
   - 사용자가 입력한 번호(목록 순번 `1, 2` 또는 일감 번호 `12010, 12008`)만 보고서에 포함되고, **Enter 입력 시 장기 건은 전체 제외**됩니다.
3. **[핵심 예외 규칙] 과거 완료 건 자동 제외**:
   - 상태가 '완료'인 일감 중:
     - '변경' 일시가 이번 주 범위(월 00:00:00 ~ 금 23:59:59) 내에 있어야 합니다.
     - **'완료기한(완료요청일)'이 이번 주 월요일보다 이전인 건은 과거 완료 보고 후 코멘트나 파일만 추가된 것이므로 목록에서 완전히 제외(탈락)**합니다.
4. **계층 구조 정렬**:
   - 상위일감 존재 시 그룹화 (평균 진척도 계산 생략) ➔ `범주별` ➔ `상위일감별` 계층 구조로 정렬됩니다.

### [Step 3] PMS 주간보고 화면 자동 입력 및 저장
- **진입 경로 (오류 방지)**:
  1. 먼저 주간보고 목록 화면(`https://pms.metabuild.co.kr/mypage/wkly/list`)으로 이동합니다.
  2. 목록 화면 우측 상단의 **`[등록]`** 버튼을 클릭하여 안전하게 등록 폼(`/mypage/wkly/save`)으로 진입합니다 (URL 직접 입력 시 발생하는 '나가겠습니까' 경고창 및 나가기 버튼 조작 오류 원천 차단).
- **진척률**: `100` 입력 (상단 요일별 배터리 시간 인풋과 구분하여 `.wkly-field-box--rate input` 정밀 타겟팅 및 Vue `v-model` 이벤트 동기화)
- **보고 내용 주입**:
  - `금주 실적 / 진척사항` ProseMirror 에디터(`div.tiptap.ProseMirror` 1번째): Step 2에서 생성한 주간보고 전체 텍스트(날짜 헤더, 요약 통계, 세부 내역 등)를 주입
  - `차주 계획 / 진행예정 사항` 에디터(`div.tiptap.ProseMirror` 2번째): 헤더를 `## 차주 계획`으로 치환하고 `(1) 수행` 요약 카운트 부분을 제거한 뒤, `[업무 세부 내역]`만 주입
- **저장 완료**:
  - 1차 상단 `[저장]` 버튼(`button.v-btn.bg-primary`) 클릭
  - 2차 SweetAlert2 확인 팝업("저장하시겠습니까?")의 `[저장]` 버튼(`button.swal2-confirm.custom-swal2-confrim`) 감지 및 클릭 후 저장 완료 처리

---

## 📝 주간보고 출력 서식 예시

```text
## 주간업무보고 (2026. 11. 02. ~ 2026. 11. 06.)


(1) 사업관리
   - 주간보고
   - 안전망시스템공지사항 등록


(2) 수행
   - 신규: 0건
   - 진행중: 4건
   - 완료(금주): 1건

[업무 세부 내역]
▶ 개인정보보호
  - 상위일감 #11737
      . [진행중] [#11737-8] (공통) 연계의뢰 대상자 중 반려 미처리 상태로 6개월 경과 시 파기 (10%)
      . [진행중] [#11737-6] (공통) 법정대리인 동의 여부가 잘못된 발굴대상자 파기 (80%)
      . [진행중] [#11737-4] (공통) 개인정보 파기 시 진행중인 사례 보유한 대상자의 경우 자동으로 사례종결 처리 (50%)

▶ 공통
  - #14056
      . [진행중] <데이터센터> 이메일 발송 중단 요청 (90%)
  - #14053
      . [완료] <데이터센터> 연계의뢰(범정부) 처리기관 내 자살예방센터 누락 건 조치 요청

※ 전체 누적 완료건(현재 기준): 249건
```

---

## 🌐 웹 브라우저용 수기 도구 (`Untitled-1.html`)

인터넷 브라우저에서 `Untitled-1.html` 파일을 더블클릭하여 열면, Redmine에서 다운로드받은 `weekly_issues.csv` 파일을 직접 업로드하여 동일한 필터링 규칙(과거 완료기한 제외, 사업관리 항목 선택, 90일 이상 장기 건 포함 옵션)으로 즉시 주간보고 텍스트를 확인하고 클립보드에 복사할 수 있습니다.
