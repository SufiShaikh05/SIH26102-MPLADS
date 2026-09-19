# SIH26102 MPLADS — MVP Specification

## 1. Objective
Build a working AI-assisted monitoring platform that analyzes MPLADS work and fund-utilization records to identify unusual patterns, potential inefficiencies, and indicators requiring human verification.

## 2. Primary MVP workflow
Real MPLADS data
→ data cleaning
→ feature engineering
→ anomaly/risk detection
→ explainable risk score
→ FastAPI
→ dashboard
→ work-level evidence and alerts

## 3. Core detection modules
1. Cost anomaly
2. Delay / stalled-work detection
3. Duplicate / near-duplicate work detection
4. Fund-utilization / expenditure anomaly
5. Combined explainable risk score

## 4. Output for every analyzed work
- work_id
- risk_score (0–100)
- risk_level
- anomaly_types
- reasons
- supporting metrics
- source fields used

## 5. MVP UI
1. Dashboard
2. Alerts / high-risk works
3. Works table
4. Work details
5. Filters

## 6. MVP non-goals
- No claim that an anomaly proves fraud.
- No chatbot.
- No real-time government-system integration in the first MVP.
- No supervised fraud classifier unless reliable labels are found.
- No advanced computer vision.
- No complex GIS.
- No unnecessary authentication/role system unless a real requirement appears.

## 7. Acceptance criteria
The deployed application must demonstrate one complete path:
dataset → analysis → high-risk work → explanation → dashboard.

A reviewer must be able to select a flagged work and understand exactly why it received its risk score.
