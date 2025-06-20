import os
import json
from pathlib import Path
from dotenv import load_dotenv

from checklist.test_suite import TestSuite
from mini_dev.evaluation.evaluation_ex import execute_model
from spinner import Spinner
from checklist.test_types import DIF, MTP, ORC


load_dotenv(override=True)
DB_ROOT_PATH = Path(os.getenv("DB_ROOT_PATH"))

sql_dialect="SQLite"
dev_file = "data/minidev/mini_dev_sqlite.json"
pred_file = "mini_dev/llm/exp_result/sql_output_kg/predict_mini_dev_gpt-4-32k_sqlite.json"
preds = [value.split('\t-----')[0] for value in json.load(open(pred_file)).values()]
data = json.load(open(dev_file))
assert len(preds) == len(data)

for pred, ex in zip(preds, data):
    db_id = ex['db_id']
    db_path = os.path.join(DB_ROOT_PATH, db_id, f"{db_id}.sqlite")
    nl = ex['question']
    hint = ex['evidence']
    gold_sql = ex['SQL']
    
    ret = execute_model(pred, gold_sql, db_path, idx=-1, meta_time_out=30.0, sql_dialect=sql_dialect)
    # nl-sql correctness label (0 means incorrect, 1 mean correct)
    label = 1 if ret['res'] == 1 else 0 

    # spinner = Spinner("Running...")
    # with spinner:
    
    # suite = TestSuite()
    test1 = ORC(nl, hint, pred, sql_dialect, db_id, db_path)
    # test2 = MTP(nl, hint, sql, sql_dialect, db_id, db_path)
    # test3 = DIF(nl, hint, sql, sql_dialect, db_id, db_path)
    # suite.add(test1)
    # suite.add(test2)
    # suite.add(test3)
    
    test1.run1()
    test1.summary1()