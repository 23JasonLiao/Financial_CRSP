# How to run

## 1. Install

```bash
pip install -r requirements.txt
```

## 2. Put the raw files in `data/`

See `data/README.md`.

## 3. Build all Step-1 events

```bash
python main.py build
```

For the full dataset this can take several minutes because the builder reads and aggregates all CRSP share-class and holdings rows.

## 4. Validate

```bash
python scripts/validate_step1.py --data-root data
```

Check `data/derived/STEP1_VALIDATION.json` and require `PASS: true` before using the event file for modeling.

## 5. Start API + dashboard

```bash
python main.py serve --host 127.0.0.1 --port 5000
```

Open:

`http://127.0.0.1:5000`
