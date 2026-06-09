# 📈 Sentiment + RSI 기반 주식 매매 신호 생성 시스템
뉴스(Finnhub)에 반영된 Sentiment와 RSI 지표를 결합하여 자동으로 매매 신호를 생성하는 프로젝트입니다.

## 🚀 Demo
- 예시: QQQ 종목 매매 신호
- BUY / SELL 시점 시각화

## ❗ Problem
기존 기술적 지표(RSI 등)는 시장의 심리적 요소를 반영하지 못하지만 뉴스에서 기반된 Sentiment 정보로 이를 보완

## 💡 Solution
- Finnhub API를 통해 뉴스 데이터 수집
- NLP 모델을 사용해 Sentiment Score 생성
- RSI 지표와 결합하여 매매 신호 생성

## 🛠️ Tech Stack
- Python
- Pandas / NumPy
- Finnhub API
- Scikit-learn or Transformer (감정 분석)

## ⚙️ Installation
```bash
git clone https://github.com/your-repo.git
cd project
pip install -r requirements.txt
```
## ⚠️ Limitations
- 뉴스 데이터 지연 문제
- 감정 분석 모델 정확도 한계
  
## 🔮 Future Work
- 실시간 스트리밍 데이터 적용
- 딥러닝 기반 감정 모델 개선

## 📚 References
- Finnhub API
- RSI Indicator


---
### 단기 목표
- 백테스팅 데이터 수집(2주 간격)
- 전략 성능 검증 및 파라미터 튜닝

### 중기 목표
- 웹 대시보드 개발(Streamlit 기반)
- 실시간 신호 시각화

### 장기 목표
- 한국투자증권 API 연동
- 자동 매매 환경의 구축
