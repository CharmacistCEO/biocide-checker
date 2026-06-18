"""살충제(살생물제) 판매제한 제품 조회 — 모바일 우선 단일 화면 앱.

흐름: 실시간 바코드 스캔 또는 상품명 입력 → 상품명 자동 조회 →
     초록누리(ecolife.mcee.go.kr) 통합검색 → 결과를 카드로 인라인 표시.
"""
from __future__ import annotations

import csv
import io
import re
import urllib.parse
from dataclasses import dataclass

import requests
import streamlit as st
from bs4 import BeautifulSoup
from PIL import Image

try:
    import zxingcpp
    DECODER_OK = True
except Exception:
    DECODER_OK = False

try:
    from streamlit_qrcode_scanner import qrcode_scanner
    SCANNER_OK = True
except Exception:
    SCANNER_OK = False

try:
    import pytesseract
    pytesseract.get_tesseract_version()
    OCR_OK = True
except Exception as _e:
    OCR_OK = False
    OCR_ERR = repr(_e)


# ─────────────────────────── 상수 ───────────────────────────

ECOLIFE_BASE = "https://ecolife.mcee.go.kr"
ECOLIFE_SEARCH_URL = (
    ECOLIFE_BASE + "/ecolife/search/integratedSearch?pMENU_NO=2076"
    "&keyword={kw}&mainSearchType={t}"
)

# 카테고리별 색상/아이콘 — 살생물제 승인 여부 판단에 중요한 순서
CATEGORY_STYLE = {
    "위반": ("🚫", "#fee2e2", "#dc2626"),       # 빨강 — 판매·유통 금지
    "승인": ("✅", "#dcfce7", "#16a34a"),       # 초록 — 살생물제 정식 승인
    "신고": ("ℹ️", "#dbeafe", "#2563eb"),       # 파랑 — 안전확인 신고
    "전성분": ("📋", "#e5e7eb", "#374151"),     # 회색 — 정보 공개
    "우수": ("🏆", "#fef3c7", "#d97706"),       # 노랑
    "자율": ("📝", "#e0e7ff", "#4338ca"),       # 보라
}


@dataclass
class EcolifeItem:
    category: str          # 카테고리 키워드 (위반/승인/신고/...)
    category_full: str     # 카테고리 풀네임 (예: "생활화학제품(승인) 검색 166건")
    product_name: str      # 상품명
    approval_no: str       # 신고/승인번호
    company: str           # 회사명
    detail_url: str        # 상세 페이지 URL


# ─────────────────────────── 핵심 로직 ───────────────────────────

def ecolife_url(keyword: str, type_code: str = "") -> str:
    return ECOLIFE_SEARCH_URL.format(kw=urllib.parse.quote(keyword), t=type_code)


def decode_barcode_image(image_bytes: bytes) -> str | None:
    """업로드된 이미지에서 바코드 디코딩."""
    if not DECODER_OK:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        for variant in (img, img.rotate(90, expand=True), img.rotate(270, expand=True)):
            results = zxingcpp.read_barcodes(variant)
            if results:
                return results[0].text
    except Exception:
        return None
    return None


# 노이즈 필터 (직전 버전 유지)
_NOISE_KEYWORDS = (
    "송장", "택배", "배송조회", "운송장", "조회한", "홍보 페이지",
    "광고", "더보기", "본문 바로가기", "이전페이지", "인플루언서",
    "코리안넷", "Dreamdepot",
)
_SITE_SUFFIX_REGEX = re.compile(
    r"(마트|스토어|쇼핑몰|쇼핑|샵|상점|닷컴|Mall|Store|Shop|Market)$",
    re.IGNORECASE,
)
_NOISE_REGEX = re.compile(
    r"^(naver|google|daum|bing|쿠팡|지마켓|옥션|11번가|티몬|위메프|네이버|다음|이마트)\b",
    re.IGNORECASE,
)
_DOMAIN_REGEX = re.compile(r"\.(co\.kr|com|net|org|kr)(/|$|\s)", re.IGNORECASE)


def _clean_candidate(raw: str) -> str:
    t = re.sub(r"\s+", " ", raw).strip()
    t = re.split(r"[ㅣ|·•‧]|\s+-\s+|\s+:\s+|\s+\|\s+", t, maxsplit=1)[0].strip()
    return t


def _is_valid_product_name(name: str, barcode: str) -> bool:
    if not name or len(name) < 6 or len(name) > 120:
        return False
    if barcode in name:
        return False
    if len(re.findall(r"[가-힣]", name)) < 2:
        return False
    if any(kw in name for kw in _NOISE_KEYWORDS):
        return False
    if _NOISE_REGEX.search(name):
        return False
    if _DOMAIN_REGEX.search(name):
        return False
    words = name.split()
    if len(words) <= 2 and _SITE_SUFFIX_REGEX.search(name):
        return False
    return True


# ─────────────────────────── 자체 제품 DB (구글 시트) ───────────────────────────

# 시트 컬럼: 상품명 / 규격 / 제형 / 표준바코드 / 대조결과 / 7월이후_판매 /
#           매칭_승인제품(참고) / 비고
SALES_STATUS_STYLE = {
    "판매가능": ("✅", "#dcfce7", "#16a34a", "판매 가능"),
    "판매중단": ("🚫", "#fee2e2", "#dc2626", "판매 중단"),
    "개별확인": ("⚠️", "#fef3c7", "#d97706", "개별 확인 필수"),
}


@dataclass
class ProductDBEntry:
    name: str              # 상품명
    spec: str              # 규격
    formulation: str       # 제형
    barcode: str           # 표준바코드
    comparison: str        # 대조결과
    sales_status: str      # 7월이후_판매
    matched_approval: str  # 매칭_승인제품(참고)
    note: str              # 비고

    @property
    def status_key(self) -> str:
        """판매상태 분류 키 — 색상 매핑용."""
        s = self.sales_status
        if "가능" in s:
            return "판매가능"
        if "중단" in s or "불가" in s:
            return "판매중단"
        return "개별확인"

    @property
    def search_keyword(self) -> str:
        """ecolife 검색에 쓸 가장 유망한 키워드."""
        return (self.matched_approval or self.name).strip()


@st.cache_data(ttl=3600, show_spinner=False)
def _load_product_db_cached(url: str) -> dict[str, ProductDBEntry]:
    """캐싱되는 내부 구현. URL이 키이므로 secrets 변경 시 자동 무효화."""
    if not url:
        return {}
    try:
        r = requests.get(url, timeout=10, allow_redirects=True)
        if r.status_code != 200 or "csv" not in r.headers.get("Content-Type", "").lower():
            return {}
        text = r.content.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        db: dict[str, ProductDBEntry] = {}
        for row in reader:
            bc = (row.get("표준바코드") or "").strip()
            if not bc:
                continue
            db[bc] = ProductDBEntry(
                name=(row.get("상품명") or "").strip(),
                spec=(row.get("규격") or "").strip(),
                formulation=(row.get("제형") or "").strip(),
                barcode=bc,
                comparison=(row.get("대조결과") or "").strip(),
                sales_status=(row.get("7월이후_판매") or "").strip(),
                matched_approval=(row.get("매칭_승인제품(참고)") or "").strip(),
                note=(row.get("비고") or "").strip(),
            )
        return db
    except Exception:
        return {}


def load_product_db() -> dict[str, ProductDBEntry]:
    """Streamlit secrets에 설정된 구글 시트 CSV URL을 fetch해서 바코드 dict로.

    캐시 키가 URL 자체이므로, secrets 변경 시 자동으로 새 캐시 항목 생성됨
    (이전 빈 dict 캐시가 막지 못함).
    """
    try:
        url = st.secrets.get("PRODUCT_DB_CSV_URL", "")
    except Exception:
        url = ""
    return _load_product_db_cached(url)


def lookup_db_by_barcode(barcode: str) -> ProductDBEntry | None:
    if not barcode:
        return None
    return load_product_db().get(barcode.strip())


def _naver_search_keys() -> tuple[str, str] | None:
    """Streamlit secrets에서 네이버 API 키 읽기."""
    try:
        cid = st.secrets.get("NAVER_CLIENT_ID", "")
        csec = st.secrets.get("NAVER_CLIENT_SECRET", "")
    except Exception:
        return None
    if cid and csec:
        return cid, csec
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def lookup_product_name(barcode: str) -> str | None:
    """바코드 → 상품명 후보 1개. 실패 시 None.

    우선순위:
      1. 네이버 검색 API (정식, 안정) — secrets에 키 설정된 경우
      2. DuckDuckGo HTML 스크래핑 (폴백)
      3. Open Food Facts (식품 한정)
    """
    if not barcode:
        return None

    # 1) 네이버 쇼핑 검색 API — 안정적, Korean-friendly
    keys = _naver_search_keys()
    if keys:
        cid, csec = keys
        try:
            r = requests.get(
                "https://openapi.naver.com/v1/search/shop.json",
                params={"query": barcode, "display": 5, "sort": "sim"},
                headers={
                    "X-Naver-Client-Id": cid,
                    "X-Naver-Client-Secret": csec,
                },
                timeout=8,
            )
            if r.status_code == 200:
                items = r.json().get("items", [])
                for it in items:
                    raw = it.get("title", "")
                    # 제목에 <b>...</b> 하이라이트 태그 있음 → 제거
                    name = re.sub(r"</?b>", "", raw).strip()
                    if _is_valid_product_name(name, barcode):
                        return name
        except Exception:
            pass

    # 2) DuckDuckGo (폴백)
    try:
        r = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": barcode},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
                ),
                "Accept-Language": "ko-KR,ko;q=0.9",
            },
            timeout=10,
        )
        if r.status_code == 200 and "result__a" in r.text:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a.result__a")[:10]:
                cleaned = _clean_candidate(a.get_text(" ", strip=True))
                if _is_valid_product_name(cleaned, barcode):
                    return cleaned
    except Exception:
        pass

    # 3) Open Food Facts
    try:
        r = requests.get(
            f"https://world.openfoodfacts.org/api/v2/product/{urllib.parse.quote(barcode)}.json",
            timeout=8,
            headers={"User-Agent": "biocide-checker/0.4"},
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == 1:
                p = data.get("product") or {}
                for key in ("product_name_ko", "product_name", "generic_name_ko", "generic_name"):
                    v = p.get(key)
                    if v and isinstance(v, str) and v.strip():
                        return v.strip()
    except Exception:
        pass

    return None


# ─────────────────────────── OCR (라벨 텍스트 추출) ───────────────────────────

# 살생물제 승인번호 패턴: 4자리-4자리 (예: 3219-0052) 또는 2자리-4자리 (2219-0365)
_APPROVAL_NO_REGEX = re.compile(r"\b\d{4}-\d{4}\b")
# 생활화학제품 신고번호 패턴: [A-Z]{1,2}\d{2}-\d{2}-\d{4} (예: CB22-12-2426)
_DCLR_NO_REGEX = re.compile(r"\b[A-Z]{1,2}\d{2}-\d{2}-\d{4}\b")


@dataclass
class OcrResult:
    approval_numbers: list[str]   # 승인/신고번호 후보
    product_name_candidates: list[str]  # 상품명 후보 (긴 한글 텍스트 줄)


def ocr_extract(image_bytes: bytes) -> OcrResult:
    """라벨 사진에서 텍스트 추출 후 의미있는 후보 정리."""
    if not OCR_OK:
        return OcrResult([], [])
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        # 한국어 + 영어 함께
        text = pytesseract.image_to_string(img, lang="kor+eng")
    except Exception:
        return OcrResult([], [])

    # 승인/신고번호 후보
    approvals = list(dict.fromkeys(
        _APPROVAL_NO_REGEX.findall(text) + _DCLR_NO_REGEX.findall(text)
    ))

    # 상품명 후보: 한글 5자 이상 라인, 길이 6~60
    raw_lines = [line.strip() for line in text.splitlines()]
    name_candidates: list[str] = []
    for line in raw_lines:
        if not line or len(line) < 6 or len(line) > 60:
            continue
        # 한글 비율 30% 이상
        hangul = len(re.findall(r"[가-힣]", line))
        if hangul < 3 or hangul / len(line) < 0.3:
            continue
        # 흔한 라벨 텍스트 (성분, 주의사항 등) 제거
        if any(skip in line for skip in (
            "성분", "주의", "용도", "보관", "유통기한", "제조원", "환경부",
            "사용방법", "응급조치", "개봉", "재활용", "허가", "함량",
        )):
            continue
        name_candidates.append(line)

    # 중복 제거
    name_candidates = list(dict.fromkeys(name_candidates))[:8]
    return OcrResult(approval_numbers=approvals, product_name_candidates=name_candidates)


def _classify(category_full: str) -> str:
    """카테고리 풀네임에서 핵심 키워드 추출."""
    for key in CATEGORY_STYLE.keys():
        if key in category_full:
            return key
    return "기타"


def _parse_li(li, category_full: str) -> EcolifeItem | None:
    """검색결과 li 한 개에서 정보 추출."""
    a = li.find("a")
    if not a:
        return None
    href = a.get("href", "")
    detail_url = ECOLIFE_BASE + href if href.startswith("/") else href

    # li 텍스트 전체에서 의미있는 토큰 추출
    text = li.get_text(" ", strip=True)
    text = re.sub(r"\s+\|\s+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # 신고번호/승인번호 패턴 (예: CB22-12-2426, 3219-0052)
    m_no = re.search(r"신고번호\s*:?\s*([A-Z0-9가-힣\-]+)", text)
    approval_no = m_no.group(1) if m_no else ""

    # 회사명 — li 마지막에 위치, 종종 마지막 a 태그 또는 마지막 토큰
    company = ""
    # 마지막 a 태그 확인
    all_a = li.find_all("a")
    if len(all_a) > 1:
        company = all_a[-1].get_text(" ", strip=True)
    if not company:
        # 폴백: 텍스트 마지막 토큰
        parts = text.rsplit(" ", 1)
        if parts:
            company = parts[-1]

    # 상품명 — 텍스트 첫 부분, "신고번호" 또는 "조치일" 앞까지
    name_text = re.split(r"(신고번호|조치일|신고일|화학제품정보)", text)[0].strip()
    # [분류] 접두사 제거 가능하지만 그대로 두는 게 정보 더 많음
    product_name = name_text[:120] if name_text else "(이름 추출 실패)"

    return EcolifeItem(
        category=_classify(category_full),
        category_full=category_full,
        product_name=product_name,
        approval_no=approval_no,
        company=company,
        detail_url=detail_url,
    )


# 제품이 아닌 게시판 카테고리 — 제외
_NON_PRODUCT_CATEGORIES = ("정보마당", "홍보마당", "공지", "Q&A", "질의응답")


def _matches_keyword(item_text: str, keyword: str) -> bool:
    """검색어가 결과 텍스트에 실제로 포함되는지 확인.

    ecolife는 한국어 형태소 단위로 부분매칭해서 조사("지/않/는")만
    매칭되어 무관한 제품을 끼워넣는다. 공백 제거 후 substring 매칭.
    """
    if not keyword or len(keyword) < 2:
        return True
    norm_text = re.sub(r"\s+", "", item_text)
    norm_kw = re.sub(r"\s+", "", keyword)
    # 4글자 이상이면 정확 매칭 요구, 짧으면 substring
    return norm_kw in norm_text


@st.cache_data(ttl=600, show_spinner=False)
def fetch_ecolife_results(keyword: str, max_per_category: int = 5) -> list[EcolifeItem]:
    """ecolife 통합검색 결과 페이지를 가져와 카드 형태로 파싱."""
    if not keyword:
        return []
    try:
        r = requests.get(
            ecolife_url(keyword),
            headers={"User-Agent": "Mozilla/5.0 (compatible; biocide-checker/0.3)"},
            timeout=12,
        )
        if r.status_code != 200:
            return []
    except Exception:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    items: list[EcolifeItem] = []
    seen_urls: set[str] = set()
    seen_signatures: set[str] = set()  # (product_name, approval_no) 기준 중복 제거

    for wrap in soup.select(".search-result-wrap"):
        h3 = wrap.select_one("h3.title-result")
        if not h3:
            continue
        category_full = h3.get_text(" ", strip=True)
        # "검색 0건"이거나 게시판 카테고리면 스킵
        if re.search(r"검색\s*0\s*건", category_full):
            continue
        if any(k in category_full for k in _NON_PRODUCT_CATEGORIES):
            continue

        lis = wrap.select("ul.result-list > li")
        added = 0
        for li in lis:
            if added >= max_per_category:
                break
            item = _parse_li(li, category_full)
            if not item:
                continue
            if item.detail_url in seen_urls:
                continue
            # 검색어가 상품명/회사명에 실제 포함되는지 확인 (ecolife 형태소 매칭 노이즈 제거)
            haystack = f"{item.product_name} {item.company} {item.approval_no}"
            if not _matches_keyword(haystack, keyword):
                continue
            # 상품명+승인번호 기준 중복 제거 (같은 제품을 다른 URL로 두 번 보여주는 경우)
            sig = re.sub(r"\s+", "", item.product_name) + "|" + item.approval_no
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)
            seen_urls.add(item.detail_url)
            items.append(item)
            added += 1

    # 정렬: 위반 > 승인 > 신고 > 나머지
    priority = {"위반": 0, "승인": 1, "신고": 2, "전성분": 3, "우수": 4, "자율": 5, "기타": 6}
    items.sort(key=lambda x: priority.get(x.category, 99))
    return items


# ─────────────────────────── UI ───────────────────────────

st.set_page_config(
    page_title="살충제 판매제한 조회",
    page_icon="🪲",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# 모바일 최적화 스타일
st.markdown(
    """
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 720px; }
    .card {
        border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px 16px;
        margin-bottom: 12px; background: #fafafa;
    }
    .badge {
        display: inline-block; padding: 3px 10px; border-radius: 999px;
        font-size: 0.78rem; font-weight: 600; margin-bottom: 6px;
    }
    .product-name { font-size: 1.05rem; font-weight: 700; margin: 4px 0 6px; color: #111; }
    .meta { color: #555; font-size: 0.88rem; line-height: 1.5; }
    .meta b { color: #333; }
    .stTextInput input { font-size: 1.05rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# 상태 초기화
ss = st.session_state
ss.setdefault("query", "")
ss.setdefault("last_scanned", None)
ss.setdefault("source_hint", "")
ss.setdefault("db_entry", None)

st.markdown("### 🪲 살충제 판매제한 조회")
st.caption("바코드 스캔 → 자동으로 초록누리(ecolife) 결과 표시")

# DB 로드 상태 — 사용자/관리자가 secrets 설정 여부 즉시 확인
_db = load_product_db()
_naver_on = bool(_naver_search_keys())
_status_line = []
if _db:
    _status_line.append(f"📚 약국 DB **{len(_db)}개** 로드됨")
else:
    _status_line.append("⚠️ 약국 DB 로드 안 됨 (Streamlit Secrets에 `PRODUCT_DB_CSV_URL` 미설정)")
if _naver_on:
    _status_line.append("🔑 네이버 API 활성")
else:
    _status_line.append("🔓 네이버 API 미설정 (DDG 폴백)")
st.caption(" · ".join(_status_line))


def set_query(new_query: str, hint: str = ""):
    """검색어를 세션 상태에 설정하고 안내 메시지 갱신."""
    ss.query = new_query
    ss.source_hint = hint


def handle_scanned_barcode(barcode: str):
    """바코드 인식 후 DB 조회 우선, 폴백으로 네이버/DDG."""
    # 1) 자체 DB 우선
    entry = lookup_db_by_barcode(barcode)
    if entry:
        ss.db_entry = entry
        set_query(entry.search_keyword, f"바코드 `{barcode}` → 약국 DB 매칭")
        return
    # 2) 네이버/DDG 폴백
    ss.db_entry = None
    with st.spinner("상품명 자동 조회 중..."):
        name = lookup_product_name(barcode)
    if name:
        set_query(name, f"바코드 `{barcode}` → 상품명 자동 인식")
    else:
        set_query("", f"바코드 `{barcode}` 인식했으나 자동 조회 실패 — 직접 입력해주세요")


# ── 입력 영역 ──
with st.container():
    # 실시간 스캐너
    if SCANNER_OK:
        scanned = qrcode_scanner(key="scanner")
        if scanned and scanned != ss.last_scanned:
            ss.last_scanned = scanned
            handle_scanned_barcode(scanned)
            st.rerun()

    # 사진 업로드 — 바코드 + OCR 동시 처리
    with st.expander("📷 라벨/바코드 사진으로 인식 (바코드 + OCR 동시)", expanded=False):
        st.caption(
            "라벨 앞면 또는 바코드를 찍어 올리세요. "
            "바코드, 승인번호, 상품명을 한 번에 인식합니다."
        )
        uploaded = st.file_uploader(
            "사진을 선택하세요",
            type=["png", "jpg", "jpeg", "webp"],
            label_visibility="collapsed",
        )
        if uploaded is not None:
            img_bytes = uploaded.getvalue()
            # 1) 바코드 시도
            code = decode_barcode_image(img_bytes)
            # 2) OCR 시도 (Tesseract 가용한 환경에서만 실제 동작)
            ocr_res = ocr_extract(img_bytes) if OCR_OK else OcrResult([], [])

            # 후보들 종합
            options: list[tuple[str, str]] = []  # (label, query_value)
            if code:
                # DB 우선
                db_entry = lookup_db_by_barcode(code)
                if db_entry:
                    ss.db_entry = db_entry
                    options.append((
                        f"📚 약국 DB 매칭: {db_entry.name} → {db_entry.search_keyword}",
                        db_entry.search_keyword,
                    ))
                else:
                    with st.spinner("바코드 상품명 조회 중..."):
                        bc_name = lookup_product_name(code)
                    if bc_name:
                        options.append((f"📦 바코드 매칭: {bc_name}", bc_name))
                    else:
                        options.append((f"🔢 바코드 번호로 검색: {code}", code))
            for ano in ocr_res.approval_numbers:
                options.append((f"✅ 승인/신고번호: {ano}", ano))
            for name in ocr_res.product_name_candidates:
                options.append((f"📝 OCR 상품명 후보: {name}", name))

            if not options:
                st.error(
                    "바코드도 텍스트도 인식하지 못했어요. "
                    "더 선명하게, 라벨이 정면으로 보이도록 다시 찍어주세요."
                )
            else:
                st.success(f"{len(options)}개 후보 인식됨 — 검색할 항목을 선택하세요")
                for idx, (label, value) in enumerate(options):
                    if st.button(label, key=f"opt_{idx}", use_container_width=True):
                        set_query(value, f"선택: {label}")
                        st.rerun()
                if not OCR_OK:
                    st.caption(
                        "ℹ️ 로컬 환경에서는 OCR이 비활성화돼 있습니다 "
                        "(Streamlit Cloud 배포본에서만 동작)."
                    )

    # 텍스트 검색 — 자동 인식 실패 시에도 같은 자리에서 폴백
    new_query = st.text_input(
        "상품명 또는 승인번호 직접 검색",
        value=ss.query,
        placeholder="예: 홈키파, 에프킬라, CB22-12-2426",
        key="query_input",
    )
    if new_query != ss.query:
        ss.query = new_query
        ss.source_hint = ""

    if ss.source_hint:
        st.caption(ss.source_hint)

# ── 결과 영역 ──

# 1) 약국 DB 매칭 결과 우선 표시 (있으면)
db_entry: ProductDBEntry | None = ss.get("db_entry")
if db_entry:
    icon, bg, fg, status_label = SALES_STATUS_STYLE.get(
        db_entry.status_key, ("📦", "#f3f4f6", "#6b7280", db_entry.sales_status or "확인 필요")
    )
    detail_rows = []
    if db_entry.name:
        detail_rows.append(f"<b>상품명</b>: {db_entry.name}")
    if db_entry.spec:
        detail_rows.append(f"<b>규격</b>: {db_entry.spec}")
    if db_entry.formulation:
        detail_rows.append(f"<b>제형</b>: {db_entry.formulation}")
    if db_entry.matched_approval:
        detail_rows.append(f"<b>매칭 승인제품</b>: {db_entry.matched_approval}")
    if db_entry.comparison:
        detail_rows.append(f"<b>대조결과</b>: {db_entry.comparison}")
    if db_entry.note:
        detail_rows.append(f"<b>비고</b>: {db_entry.note}")
    detail_html = "<br>".join(detail_rows)

    st.markdown(
        f"""
        <div class="card" style="border-left:6px solid {fg}; background:{bg};">
            <div style="font-size:0.85rem;font-weight:600;color:{fg};margin-bottom:4px;">
                📚 약국 DB 매칭 결과
            </div>
            <div style="font-size:1.4rem;font-weight:800;color:{fg};margin-bottom:8px;">
                {icon} {status_label}
            </div>
            <div class="meta">{detail_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

query = (ss.query or "").strip()
if not query:
    st.info(
        "💡 카메라로 바코드를 스캔하거나, 위 칸에 상품명을 입력하세요. "
        "라벨에 적힌 **승인번호**(예: `CB22-12-2426`)로 검색하면 가장 정확합니다."
    )
    st.stop()

with st.spinner(f"초록누리에서 `{query}` 검색 중..."):
    results = fetch_ecolife_results(query)

if not results:
    st.warning("초록누리에서 일치하는 결과를 찾지 못했어요.")
    st.link_button(
        "🔗 초록누리 사이트에서 직접 확인",
        ecolife_url(query),
        use_container_width=True,
    )
    st.stop()

# 결과 헤더
st.markdown(f"##### 🔎 검색결과 — `{query}` ({len(results)}건)")

# 승인 여부 한눈 요약
has_aprv = any(it.category == "승인" for it in results)
has_dclr = any(it.category == "신고" for it in results)
has_viol = any(it.category == "위반" for it in results)
summary_parts = []
if has_viol:
    summary_parts.append("🚫 **위반제품 있음** — 판매 금지")
if has_aprv:
    summary_parts.append("✅ 살생물제 **승인** 제품 있음")
if has_dclr:
    summary_parts.append("ℹ️ 안전확인 **신고** 제품 있음")
if summary_parts:
    st.markdown(" · ".join(summary_parts))

# 결과 카드
for item in results:
    icon, bg, fg = CATEGORY_STYLE.get(item.category, ("📦", "#f3f4f6", "#6b7280"))
    badge_html = (
        f'<span class="badge" style="background:{bg};color:{fg}">'
        f'{icon} {item.category_full.split(" 검색")[0]}'
        f"</span>"
    )
    meta_parts = []
    if item.approval_no:
        meta_parts.append(f"<b>승인/신고번호</b>: {item.approval_no}")
    if item.company:
        meta_parts.append(f"<b>제조사</b>: {item.company}")
    meta_html = "<br>".join(meta_parts) if meta_parts else ""

    st.markdown(
        f"""
        <div class="card">
            {badge_html}
            <div class="product-name">{item.product_name}</div>
            <div class="meta">{meta_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.link_button("자세히 보기 →", item.detail_url, use_container_width=False)

# 전체 결과 보기 링크
st.divider()
st.link_button(
    "🔗 초록누리에서 전체 결과 보기",
    ecolife_url(query),
    use_container_width=True,
)
