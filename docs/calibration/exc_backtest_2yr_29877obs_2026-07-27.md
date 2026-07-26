# Exceptionalism Calibration Report

- Rows analysed: **29877** (with exceptionalism verdict: 29877)
- Forward-return coverage by horizon: {1: 29877, 3: 29877, 5: 29877, 10: 29877, 20: 29877}

## 1-Day Forward Horizon

**Legacy vs Exceptionalism: ❌ not yet**

| Selection set | N | Hit % | Avg % | Avg MaxDD % |
| --- | --- | --- | --- | --- |
| Legacy (final_selected) | 11507 | 46.1 | 0.04 | -1.81 |
| Exceptionalism (qualified) | 335 | 42.7 | -0.02 | -2.11 |


Gate precision **42.7%** · false-positive rate **57.3%** (n=335).

**Forward return by exceptionalism band:**

| EXC band | N | Hit % | Avg % | Avg MaxDD % |
| --- | --- | --- | --- | --- |
| 0-59 | 27317 | 45.1 | 0.01 | -1.77 |
| 60-69 | 1794 | 45.8 | 0.05 | -2.03 |
| 70-79 | 613 | 49.3 | 0.21 | -1.98 |
| 80-89 | 136 | 44.1 | 0.83 | -2.04 |
| 90-100 | 17 | 52.9 | 0.39 | -2.31 |


**Optimal cutoff (this horizon): EXC ≥ 84** → hit 52.0%, avg 1.18%, n=75.

**Empirical threshold-by-health curve** (data-driven `required_exceptionalism`):

| Health | N | Suggested EXC | Hit % | Avg % |
| --- | --- | --- | --- | --- |
| BEAR | 12476 | 84 | 55.6 | 1.64 |
| CORRECTION | 2854 | 78 | 68.4 | 1.3 |
| SIDEWAYS | 3448 | 76 | 43.8 | 0.82 |
| WEAK_BULL | 9754 | 84 | 55.0 | 1.7 |
| STRONG_BULL | 1345 | 56 | 39.4 | -0.29 |


## 3-Day Forward Horizon

**Legacy vs Exceptionalism: ❌ not yet**

| Selection set | N | Hit % | Avg % | Avg MaxDD % |
| --- | --- | --- | --- | --- |
| Legacy (final_selected) | 11507 | 44.6 | -0.08 | -3.46 |
| Exceptionalism (qualified) | 335 | 39.7 | -0.51 | -4.57 |


Gate precision **39.7%** · false-positive rate **60.3%** (n=335).

**Forward return by exceptionalism band:**

| EXC band | N | Hit % | Avg % | Avg MaxDD % |
| --- | --- | --- | --- | --- |
| 0-59 | 27317 | 45.2 | 0.03 | -3.36 |
| 60-69 | 1794 | 47.2 | 0.1 | -3.82 |
| 70-79 | 613 | 46.7 | 0.32 | -3.77 |
| 80-89 | 136 | 44.9 | 0.33 | -4.29 |
| 90-100 | 17 | 35.3 | -0.5 | -5.02 |


**Optimal cutoff (this horizon): EXC ≥ 74** → hit 48.1%, avg 0.72%, n=428.

**Empirical threshold-by-health curve** (data-driven `required_exceptionalism`):

| Health | N | Suggested EXC | Hit % | Avg % |
| --- | --- | --- | --- | --- |
| BEAR | 12476 | 84 | 47.2 | 1.21 |
| CORRECTION | 2854 | 78 | 52.6 | 1.64 |
| SIDEWAYS | 3448 | 78 | 51.3 | 2.57 |
| WEAK_BULL | 9754 | 60 | 49.1 | 0.23 |
| STRONG_BULL | 1345 | 54 | 26.1 | -1.9 |


## 5-Day Forward Horizon

**Legacy vs Exceptionalism: ❌ not yet**

| Selection set | N | Hit % | Avg % | Avg MaxDD % |
| --- | --- | --- | --- | --- |
| Legacy (final_selected) | 11507 | 45.3 | -0.08 | -4.61 |
| Exceptionalism (qualified) | 335 | 43.6 | -0.02 | -5.59 |


Gate precision **43.6%** · false-positive rate **56.4%** (n=335).

**Forward return by exceptionalism band:**

| EXC band | N | Hit % | Avg % | Avg MaxDD % |
| --- | --- | --- | --- | --- |
| 0-59 | 27317 | 45.1 | -0.04 | -4.47 |
| 60-69 | 1794 | 45.0 | 0.0 | -5.05 |
| 70-79 | 613 | 48.5 | 0.57 | -5.15 |
| 80-89 | 136 | 52.2 | 1.53 | -5.75 |
| 90-100 | 17 | 52.9 | -0.98 | -7.35 |


**Optimal cutoff (this horizon): EXC ≥ 84** → hit 53.3%, avg 1.27%, n=75.

**Empirical threshold-by-health curve** (data-driven `required_exceptionalism`):

| Health | N | Suggested EXC | Hit % | Avg % |
| --- | --- | --- | --- | --- |
| BEAR | 12476 | 84 | 63.9 | 4.05 |
| CORRECTION | 2854 | 78 | 52.6 | 0.26 |
| SIDEWAYS | 3448 | 78 | 53.8 | 2.08 |
| WEAK_BULL | 9754 | 82 | 51.9 | 0.73 |
| STRONG_BULL | 1345 | 58 | 35.3 | -0.81 |


## 10-Day Forward Horizon

**Legacy vs Exceptionalism: ✅ improved**

| Selection set | N | Hit % | Avg % | Avg MaxDD % |
| --- | --- | --- | --- | --- |
| Legacy (final_selected) | 11507 | 44.8 | -0.05 | -6.55 |
| Exceptionalism (qualified) | 335 | 45.7 | 0.13 | -7.66 |


Gate precision **45.7%** · false-positive rate **54.3%** (n=335).

**Forward return by exceptionalism band:**

| EXC band | N | Hit % | Avg % | Avg MaxDD % |
| --- | --- | --- | --- | --- |
| 0-59 | 27317 | 43.7 | -0.08 | -6.45 |
| 60-69 | 1794 | 45.7 | 0.28 | -7.07 |
| 70-79 | 613 | 50.6 | 1.57 | -7.06 |
| 80-89 | 136 | 54.4 | 2.27 | -7.84 |
| 90-100 | 17 | 47.1 | -3.09 | -11.35 |


**Optimal cutoff (this horizon): EXC ≥ 74** → hit 51.9%, avg 2.22%, n=428.

**Empirical threshold-by-health curve** (data-driven `required_exceptionalism`):

| Health | N | Suggested EXC | Hit % | Avg % |
| --- | --- | --- | --- | --- |
| BEAR | 12476 | 84 | 58.3 | 5.82 |
| CORRECTION | 2854 | 78 | 47.4 | 1.02 |
| SIDEWAYS | 3448 | 74 | 50.0 | 1.54 |
| WEAK_BULL | 9754 | 82 | 70.4 | 3.06 |
| STRONG_BULL | 1345 | 70 | 45.9 | -1.52 |


## 20-Day Forward Horizon

**Legacy vs Exceptionalism: ✅ improved**

| Selection set | N | Hit % | Avg % | Avg MaxDD % |
| --- | --- | --- | --- | --- |
| Legacy (final_selected) | 11507 | 43.8 | -0.06 | -9.21 |
| Exceptionalism (qualified) | 335 | 48.1 | 1.0 | -9.99 |


Gate precision **48.1%** · false-positive rate **51.9%** (n=335).

**Forward return by exceptionalism band:**

| EXC band | N | Hit % | Avg % | Avg MaxDD % |
| --- | --- | --- | --- | --- |
| 0-59 | 27317 | 43.0 | -0.1 | -9.17 |
| 60-69 | 1794 | 43.8 | 0.15 | -9.79 |
| 70-79 | 613 | 48.8 | 2.24 | -9.54 |
| 80-89 | 136 | 51.5 | 3.15 | -10.43 |
| 90-100 | 17 | 47.1 | -4.6 | -16.34 |


**Optimal cutoff (this horizon): EXC ≥ 78** → hit 51.8%, avg 3.41%, n=222.

**Empirical threshold-by-health curve** (data-driven `required_exceptionalism`):

| Health | N | Suggested EXC | Hit % | Avg % |
| --- | --- | --- | --- | --- |
| BEAR | 12476 | 84 | 61.1 | 10.43 |
| CORRECTION | 2854 | 78 | 52.6 | 4.09 |
| SIDEWAYS | 3448 | 76 | 47.9 | 0.33 |
| WEAK_BULL | 9754 | 82 | 59.3 | 2.95 |
| STRONG_BULL | 1345 | 70 | 43.2 | -1.27 |


---
_Read-only analysis. No engine or flag was modified. Enable production flags only after these tables confirm a real improvement._