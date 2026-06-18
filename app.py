"""살충제(살생물제) 판매제한 제품 조회 도우미

흐름: 카메라/사진으로 바코드 인식 → 상품명 자동 조회 → 초록누리(ecolife.mcee.go.kr)
     통합검색 URL로 새 탭 오픈하여 승인/신고/위반 여부 확인.

데이터 소스:
  - 바코드 디코딩: zxing-cpp (prebuilt wheel, ZBar 의존성 없음)
  - 상품명 조회: Open Food Facts API → 네이버 모바일 통합검색 스크래핑 폴백
"""
from __future__ import annotations

import io
import re
import urllib.parse

import requests
import streamlit as st
from bs4 import BeautifulSoup
from PIL import Image

try:
    import zxingcpp
    DECODER_OK = True
    DECODER_ERR = ""
except Exception as e:
    DECODER_OK = False
    DECODER_ERR = repr(e)


# ─────────────────────────── Constants ───────────────────────────

ECOLIFE_BASE = "https://ecolife.mcee.go.kr/ecolife/search/integratedSearch"
ECOLIFE_MENU_NO = "2076"

# 초록누리 통합검색 mainSearchType — 사이트 select에서 추출
ECOLIFE_TYPES = {
    "전체": "",
    "생활화학제품(승인) ← 살생물제 승인": "safeAprvProd",
    "생활화학제품(신고)": "safeDclrProd",
    "위반제품": "violatePrd",
}

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


# ─────────────────────────── Helpers ───────────────────────────

def ecolife_url(keyword: str, type_code: str) -> str:
    return (
        f"{ECOLIFE_BASE}?pMENU_NO={ECOLIFE_MENU_NO}"
        f"&keyword={urllib.parse.quote(keyword)}"
        f"&mainSearchType={type_code}"
    )


def decode_barcode(image_bytes: bytes):
    """이미지 바이트 → (바코드 문자열, 포맷, 에러메시지)"""
    if not DECODER_OK:
        return None, None, f"바코드 디코더 로드 실패: {DECODER_ERR}"
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        # zxingcpp는 PIL 이미지를 바로 받음
        results = zxingcpp.read_barcodes(img)
        if not results:
            # 회전/확대 등 단순 재시도
            for angle in (90, 180, 270):
                rotated = img.rotate(angle, expand=True)
                results = zxingcpp.read_barcodes(rotated)
                if results:
                    break
        if not results:
            return None, None, "바코드를 찾지 못했습니다. 조명·초점·거리를 조절해보세요."
        r = results[0]
        fmt = getattr(r.format, "name", str(r.format))
        return r.text, fmt, None
    except Exception as e:
        return None, None, f"디코딩 오류: {e}"


@st.cache_data(ttl=3600, show_spinner=False)
def lookup_product_name(barcode: str) -> list[tuple[str, str]]:
    """바코드로 상품명 후보 검색. (소스, 상품명) 리스트 반환."""
    if not barcode:
        return []

    candidates: list[tuple[str, str]] = []

    # 1) Open Food Facts — 글로벌 무료 DB, 일부 한국 제품 포함
    try:
        r = requests.get(
            f"https://world.openfoodfacts.org/api/v2/product/{urllib.parse.quote(barcode)}.json",
            timeout=8,
            headers={"User-Agent": "ecolife-lookup/0.1 (personal use)"},
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == 1:
                p = data.get("product") or {}
                for key in ("product_name_ko", "product_name", "generic_name_ko", "generic_name"):
                    v = p.get(key)
                    if v and isinstance(v, str) and v.strip():
                        candidates.append(("Open Food Facts", v.strip()))
                        break
    except Exception:
        pass

    # 2) 네이버 모바일 통합검색 스크래핑
    try:
        url = f"https://m.search.naver.com/search.naver?query={urllib.parse.quote(barcode)}"
        r = requests.get(
            url,
            headers={"User-Agent": MOBILE_UA, "Accept-Language": "ko-KR,ko;q=0.9"},
            timeout=8,
        )
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            selectors = [
                "a.tit",
                "strong.tit",
                "div.title_area a",
                "a.api_txt_lines.total_tit",
                "h3.tit",
                "strong.title",
            ]
            found: list[str] = []
            for sel in selectors:
                for el in soup.select(sel):
                    t = el.get_text(" ", strip=True)
                    t = re.sub(r"\s+", " ", t)
                    if not t or len(t) < 3 or len(t) > 200:
                        continue
                    if barcode in t:
                        continue
                    if t.lower().startswith(("네이버", "더보기", "광고")):
                        continue
                    found.append(t)
                if len(found) >= 3:
                    break
            for t in found[:5]:
                candidates.append(("네이버 검색", t))
    except Exception:
        pass

    # 중복 제거
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for src, name in candidates:
        k = re.sub(r"\s+", " ", name).strip().lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append((src, name))
    return uniq


# ─────────────────────────── UI ───────────────────────────

st.set_page_config(page_title="살충제 판매제한 조회", page_icon="🪲", layout="centered")

st.title("🪲 살충제 판매제한 제품 조회")
st.caption("바코드 촬영 → 상품명 자동 조회 → 초록누리(ecolife) 살생물제 승인 여부 확인")

with st.expander("ℹ️ 사용 안내 / 주의", expanded=False):
    st.markdown(
        """
        - 2026년 7월부터 살충제 등 일부 살생물제는 환경부 **승인 제품만** 판매·유통 가능
        - 가장 정확한 검색은 제품 라벨에 표기된 **승인번호**로 직접 검색하는 것
        - 이 도구는 차선책으로 **바코드 → 상품명 → 초록누리 통합검색** 흐름을 자동화합니다
        - 상품명 자동 조회는 100% 정확하지 않습니다. 후보가 이상하면 직접 수정 후 검색하세요
        - 검색 결과 페이지(ecolife)는 **새 탭**에서 열립니다
        """
    )

if not DECODER_OK:
    st.error(f"바코드 디코더 로드 실패 — `pip install zxing-cpp` 후 재실행 필요\n\n{DECODER_ERR}")

# 1단계: 바코드 입력
st.subheader("1️⃣ 바코드 인식")
tab_cam, tab_upload, tab_manual = st.tabs(["📷 카메라", "🖼️ 사진 업로드", "⌨️ 직접 입력"])

new_barcode: str | None = None
detected_format: str | None = None

with tab_cam:
    st.caption("휴대폰 브라우저에서 열면 후면 카메라로 바로 촬영 가능")
    cam_img = st.camera_input("바코드를 화면 중앙에 맞춰 촬영")
    if cam_img is not None and DECODER_OK:
        with st.spinner("바코드 디코딩 중..."):
            code, fmt, err = decode_barcode(cam_img.getvalue())
        if code:
            new_barcode = code
            detected_format = fmt
            st.success(f"✅ 인식 완료: `{code}` ({fmt})")
        else:
            st.error(err)

with tab_upload:
    up = st.file_uploader("바코드 사진 파일 선택", type=["png", "jpg", "jpeg", "webp", "bmp"])
    if up is not None and DECODER_OK:
        with st.spinner("바코드 디코딩 중..."):
            code, fmt, err = decode_barcode(up.getvalue())
        if code:
            new_barcode = code
            detected_format = fmt
            st.success(f"✅ 인식 완료: `{code}` ({fmt})")
            st.image(up, caption="업로드한 이미지", width=300)
        else:
            st.error(err)
            st.image(up, caption="인식 실패 — 더 선명한 사진으로 재시도", width=300)

with tab_manual:
    typed = st.text_input(
        "바코드 번호 직접 입력",
        value=st.session_state.get("barcode", ""),
        placeholder="예: 8801234567890",
        key="manual_input",
    )
    if typed and typed.strip():
        new_barcode = typed.strip()

if new_barcode:
    st.session_state.barcode = new_barcode
    if detected_format:
        st.session_state.barcode_format = detected_format

# 2단계: 상품명 조회
if st.session_state.get("barcode"):
    st.divider()
    st.subheader("2️⃣ 상품명 조회")
    bc = st.session_state.barcode
    with st.spinner(f"바코드 `{bc}`로 상품명 조회 중..."):
        candidates = lookup_product_name(bc)

    default_name = ""
    if candidates:
        labels = [f"[{src}] {name}" for src, name in candidates]
        idx = st.radio(
            "후보 상품명",
            options=list(range(len(labels))),
            format_func=lambda i: labels[i],
            index=0,
        )
        default_name = candidates[idx][1]
    else:
        st.warning(
            "자동 조회 결과가 없습니다. 상품명을 라벨에서 확인하여 아래에 직접 입력하세요."
        )

    # 3단계: 초록누리 검색
    st.divider()
    st.subheader("3️⃣ 초록누리(ecolife) 검색")

    search_term = st.text_input(
        "검색어 (필요시 수정)",
        value=default_name,
        placeholder="예: ○○○ 살충제",
    )
    type_label = st.selectbox(
        "검색 범위",
        list(ECOLIFE_TYPES.keys()),
        index=0,
        help="살생물제 승인 여부 확인은 '생활화학제품(승인)' 또는 '전체'를 추천",
    )

    col1, col2 = st.columns(2)
    with col1:
        if search_term and search_term.strip():
            url = ecolife_url(search_term.strip(), ECOLIFE_TYPES[type_label])
            st.link_button(
                "🔍 상품명으로 검색",
                url,
                use_container_width=True,
                type="primary",
            )
        else:
            st.button(
                "🔍 상품명으로 검색", disabled=True, use_container_width=True,
                help="검색어를 입력하세요",
            )
    with col2:
        url_bc = ecolife_url(bc, ECOLIFE_TYPES[type_label])
        st.link_button(
            "🔢 바코드 번호로 검색",
            url_bc,
            use_container_width=True,
        )

    with st.expander("열릴 URL 보기"):
        if search_term and search_term.strip():
            st.code(ecolife_url(search_term.strip(), ECOLIFE_TYPES[type_label]))
        st.code(url_bc)

# 초기화
st.divider()
if st.button("🔄 초기화", help="입력한 모든 정보 삭제"):
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.rerun()

st.caption(
    "데이터 소스: 초록누리(환경부 화학제품관리시스템), Open Food Facts, 네이버 검색 · "
    "본 도구는 보조 수단이며 최종 확인은 ecolife 공식 검색 결과로 하세요."
)
