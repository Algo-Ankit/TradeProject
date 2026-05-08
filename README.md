# Production-Grade Systematic Algorithmic Trading System

A high-performance, event-driven algorithmic trading system designed for the National Stock Exchange (NSE) of India. This system integrates real-time data ingestion, advanced feature engineering, multi-strategy execution, and rigorous risk management.

## 🚀 Features

- **Live Data Ingestion**: Multi-broker support for Zerodha (Kite Connect) and Shoonya (NorenAPI) with automated reconnection and heartbeat monitoring.
- **Advanced Feature Engineering**: 
    - **Microstructure**: Order Flow Imbalance (OFI), VPIN, Bid-Ask Spread dynamics, Microprice.
    - **Options**: Real-time Greeks calculation (Delta, Gamma, Vega, Theta) and Implied Volatility (IV) surface analysis.
    - **Statistical**: Parkinson Volatility, Regime Detection via Hidden Markov Models (HMM), Momentum Z-scores.
- **Multi-Strategy Architecture**:
    - **StatArb**: Pairs trading with Kalman Filter-based dynamic hedge ratios and cointegration testing.
    - **ML Directional**: Directional predictors using LightGBM/ONNX models with Walk-Forward validation.
    - **Options RV**: Relative Value scanning for Volatility Arbitrage.
- **Execution Engine**: Smart order routing, slicing for large orders, and passive-to-market order upgrades.
- **Risk Management**:
    - **Pre-Trade**: Order-level limits, Buying Power checks, ADV-based volume limits.
    - **Real-Time**: Portfolio drawdown monitoring, exposure limits, and an automated Kill Switch.
- **Infrastructure**:
    - **ClickHouse**: Time-series storage for ticks, features, and trade logs.
    - **Kafka**: High-throughput message bus for inter-module communication.
    - **Redis**: Low-latency state management for live positions and features.
- **Monitoring**: Real-time Streamlit dashboard for PnL, risk utilization, and signal heatmaps.

## 🛠️ Tech Stack

- **Language**: Python 3.11+
- **Data Processing**: Polars, NumPy, SciPy
- **Machine Learning**: LightGBM, ONNX, Scikit-learn
- **Storage**: ClickHouse, Redis
- **Messaging**: Apache Kafka
- **Visualization**: Streamlit, Plotly

## 📂 Project Structure

```text
trading-system/
├── config/             # YAML configurations and secrets templates
├── core/               # Event bus, market clock, and base configurations
├── data/               # Ingestion feeds, quality validation, and storage writers
├── execution/          # Broker clients and smart execution logic
├── features/           # Feature pipeline and mathematical calculators
├── infra/              # Docker Compose for ClickHouse, Kafka, Redis
├── monitoring/         # Streamlit dashboard and alerting bots
├── research/           # Backtester engine and performance reporting
├── risk/               # Pre-trade and real-time risk managers
├── scripts/            # Bootstrap and backfill utilities
├── strategies/         # Strategy implementations (StatArb, ML, Options)
└── tests/              # Comprehensive unit and integration tests
```

## ⚙️ Setup & Installation

### 1. Prerequisites
- Docker & Docker Compose
- Python 3.11+
- [Kite Connect API](https://kite.trade/) / [Shoonya API](https://prism.shoonya.com/) credentials

### 2. Infrastructure Setup
Use the provided bootstrap script to start ClickHouse, Kafka, and Redis:
```bash
bash scripts/bootstrap.sh
```

### 3. Python Environment
```bash
pip install -r requirements.txt
```

### 4. Configuration
1. Copy `config/secrets.env.template` to `config/secrets.env`.
2. Fill in your API keys and database credentials.

## 📈 Usage

### Backfilling Data
```bash
python scripts/backfill.py --from 2024-01-01 --to 2024-12-31
```

### Running Backtests
```bash
python scripts/run_backtest.py --months 6 --symbols RELIANCE,TCS,INFY
```

### Starting the Live Dashboard
```bash
streamlit run monitoring/dashboard/app.py
```

## 🛡️ License
Proprietary. Developed by [Ankit Anand Singh](https://github.com/Algo-Ankit).
