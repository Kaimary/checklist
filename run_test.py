import os
import json
from pathlib import Path
from dotenv import load_dotenv
from torch.utils.data import DataLoader

from checklist.test_suite import TestSuite
from core.chat_manager import ChatManager
from core.const import SYSTEM_NAME
from evaluation.bird_evaluation.evaluation import execute_model
from load_sft_dataset import GenerationDataset
from spinner import Spinner
from checklist.test_types import DIF, MTP, ORC


load_dotenv(override=True)
DB_ROOT_PATH = Path(os.getenv("DB_ROOT_PATH"))

sql_dialect="SQLite"
train_file = "data/codes/sft_bird_with_evidence_train_text2sql.json"
dev_file = "data/codes/sft_bird_with_evidence_dev_text2sql.json"
pred_file = "evaluation/bird_evaluation/exp_result/sql_output_kg/predict_dev_codes_sqlite.json"
preds = [value.split('\t-----')[0] for value in json.load(open(pred_file)).values()]
data = json.load(open(dev_file))
assert len(preds) == len(data)


test_manager = ChatManager(train_data_path=train_file, dev_data_path=dev_file,
                           dataset_name="bird", sql_tool_path="model/codes-3b-bird-with-evidence",
                           log_path="log.txt", record_llm_path="./model/codes-3b")

eval_set = GenerationDataset(
    dev_file, test_manager.sqltool.tokenizer, 4096 - 256, "agent", 6, 10, "./sic_ckpts")
dataloader = DataLoader(eval_set, batch_size=1)

for idx, (pred, ex, batch_data) in enumerate(zip(preds, data, dataloader)):
    db_id = ex['db_id']
    db_path = os.path.join(DB_ROOT_PATH, db_id, f"{db_id}.sqlite")
    nl = ex['question']
    hint = ex['evidence']
    gold_sql = ex['SQL']
    
    filtered_items = {"schema_sequence": batch_data["schema_sequence"][0],
                      "content_sequence": batch_data["content_sequence"][0], "text": batch_data["text"][0]}
    try:
        exec_result=test_manager.sql_refiner.execute_sql(db_path, pred)
    except Exception as e:
        continue
    msg = {
            'idx': idx,
            'db_path': db_path,
            'filtered_items': filtered_items,
            'schema_items': ex["schema"]["schema_items"],
            "question": nl,
            'evidence': hint,
            'exec_result': exec_result,
            'ground_truth': gold_sql,
            'send_to': SYSTEM_NAME
        }
    test_manager.start(msg)
    review_pass=msg.get("review_pass",True)
    refined_sql = msg.get("refined_sql", "SQL placeholder")
    if review_pass!= True and refined_sql != "SQL placeholder":
        refined_sql = msg['refined_sql']


    ret = execute_model(pred, gold_sql, db_path, idx=-1, meta_time_out=30.0)
    # nl-sql correctness label (0 means incorrect, 1 mean correct)
    label = 1 if ret['res'] == 1 else 0 

    # spinner = Spinner("Running...")
    # with spinner:
    
    # suite = TestSuite()
    # test1 = ORC(nl, hint, pred, sql_dialect, db_id, db_path)
    # test2 = MTP(nl, hint, pred, sql_dialect, db_id, db_path)
    test3 = DIF(nl, hint, pred, sql_dialect, db_id, db_path)
    # suite.add(test1)
    # suite.add(test2)
    # suite.add(test3)
    
    test3.run1()
    test3.summary1()