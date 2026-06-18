# biocide-checker

살충제(살생물제) 판매제한 제품 조회 도우미.

바코드 촬영 → 상품명 자동 조회 → 환경부 **초록누리(ecolife.mcee.go.kr)** 통합검색으로
승인·신고·위반 여부 확인.

2026년 7월부터 일부 살생물제는 환경부 승인 제품만 판매·유통 가능합니다.
이 도구는 매장에서 빠르게 확인할 수 있도록 만든 보조 수단입니다.

## 실행

```powershell
pip install -r requirements.txt
streamlit run app.py
```

## 배포 (Streamlit Community Cloud)

1. https://share.streamlit.io 에서 GitHub 연결
2. 이 리포 선택 → Deploy
3. 휴대폰 브라우저로 `https://<앱이름>.streamlit.app` 접속

## 데이터 소스

- 바코드 디코딩: [zxing-cpp](https://github.com/zxing-cpp/zxing-cpp)
- 상품명 조회: Open Food Facts API + 네이버 모바일 검색
- 검색 대상: 환경부 화학제품관리시스템(초록누리)
