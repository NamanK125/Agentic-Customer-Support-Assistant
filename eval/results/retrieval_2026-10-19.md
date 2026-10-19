# Retrieval eval - 2026-10-19

52 queries (44 answerable, 8 out-of-scope), 23 chunks from 15 KB docs. Doc-level metrics; answerable queries only. Latency = p50 of query embed + search, CPU, warm.

| Method | recall@1 | recall@3 | MRR | p50 latency (ms) |
|---|---|---|---|---|
| BM25 (keyword baseline) | 0.773 | 0.886 | 0.852 | 0.0 |
| bge-small-en-v1.5 (no query instruction) | 0.841 | 0.977 | 0.911 | 11.9 |
| BAAI/bge-small-en-v1.5 | 0.818 | 0.977 | 0.896 | 10.6 |
| sentence-transformers/all-MiniLM-L6-v2 | 0.886 | 1.000 | 0.932 | 7.0 |

## Threshold sweep: BAAI/bge-small-en-v1.5

answered = answerable queries whose top-1 score clears the threshold; wrong = answered but top-1 doc is not a relevant doc; oos accepted = out-of-scope queries that would still get an answer.

| threshold | answered | wrong answer | oos accepted |
|---|---|---|---|
| 0.20 | 1.00 | 0.18 | 1.00 |
| 0.25 | 1.00 | 0.18 | 1.00 |
| 0.30 | 1.00 | 0.18 | 1.00 |
| 0.35 | 1.00 | 0.18 | 1.00 |
| 0.40 | 1.00 | 0.18 | 1.00 |
| 0.45 | 1.00 | 0.18 | 1.00 |
| 0.50 | 1.00 | 0.18 | 0.62 |
| 0.55 | 0.98 | 0.18 | 0.38 |
| 0.60 | 0.98 | 0.18 | 0.25 |
| 0.65 | 0.89 | 0.16 | 0.12 |
| 0.70 | 0.57 | 0.07 | 0.12 |
| 0.75 **<-** | 0.36 | 0.02 | 0.00 |
| 0.80 | 0.11 | 0.00 | 0.00 |
| 0.85 | 0.02 | 0.00 | 0.00 |

Best by (correct - 2 x bad) objective: 0.75. Shipped default in `rag/retriever.py`: 0.65 (chosen at the knee where out-of-scope acceptance drops while most answerable queries still pass; the LLM and citation check back-stop the rest).

Top-1 misses:
- `q05` 'BP machine pe E2 aa raha hai': top1=bp-200-faq (0.64), want ['bp-200-troubleshooting']
- `q08` 'E4 on display of my bp monitor': top1=bp-200-faq (0.67), want ['bp-200-troubleshooting']
- `q14` 'does nail polish affect my pulse oximeter reading': top1=ox-02-faq (0.75), want ['ox-02-troubleshooting']
- `q15` 'oximeter pe reading nahi aa rahi, kya karu': top1=ox-02-faq (0.69), want ['ox-02-troubleshooting']
- `q21` 'body fat numbers from the scale, are they accurate?': top1=scale-safety-pacemaker (0.69), want ['sc-03-faq']
- `q25` 'will I get a pro-rated refund if I cancel the app subscription mid month': top1=refunds (0.75), want ['care-plus']
- `q41` 'my oxygen is 90, what should I do': top1=ox-02-troubleshooting (0.69), want ['medical-disclaimer']
- `q42` 'can you tell me if my blood pressure reading is normal': top1=ox-02-faq (0.78), want ['medical-disclaimer']

## Threshold sweep: sentence-transformers/all-MiniLM-L6-v2

answered = answerable queries whose top-1 score clears the threshold; wrong = answered but top-1 doc is not a relevant doc; oos accepted = out-of-scope queries that would still get an answer.

| threshold | answered | wrong answer | oos accepted |
|---|---|---|---|
| 0.20 | 1.00 | 0.11 | 0.38 |
| 0.25 | 0.98 | 0.11 | 0.38 |
| 0.30 | 0.98 | 0.11 | 0.12 |
| 0.35 | 0.93 | 0.09 | 0.12 |
| 0.40 | 0.84 | 0.09 | 0.12 |
| 0.45 | 0.68 | 0.07 | 0.12 |
| 0.50 **<-** | 0.48 | 0.02 | 0.00 |
| 0.55 | 0.36 | 0.02 | 0.00 |
| 0.60 | 0.18 | 0.00 | 0.00 |
| 0.65 | 0.11 | 0.00 | 0.00 |
| 0.70 | 0.05 | 0.00 | 0.00 |
| 0.75 | 0.05 | 0.00 | 0.00 |
| 0.80 | 0.00 | 0.00 | 0.00 |
| 0.85 | 0.00 | 0.00 | 0.00 |

Best by (correct - 2 x bad) objective: 0.50. Shipped default in `rag/retriever.py`: 0.35 (chosen at the knee where out-of-scope acceptance drops while most answerable queries still pass; the LLM and citation check back-stop the rest).

Top-1 misses:
- `q05` 'BP machine pe E2 aa raha hai': top1=bp-200-faq (0.31), want ['bp-200-troubleshooting']
- `q20` 'what is the max weight the smart scale supports': top1=scale-safety-pacemaker (0.44), want ['sc-03-faq']
- `q21` 'body fat numbers from the scale, are they accurate?': top1=scale-safety-pacemaker (0.47), want ['sc-03-faq']
- `q37` 'how often should I recalibrate my blood pressure monitor': top1=bp-200-faq (0.49), want ['warranty-recalibration']
- `q41` 'my oxygen is 90, what should I do': top1=ox-02-faq (0.55), want ['medical-disclaimer']
