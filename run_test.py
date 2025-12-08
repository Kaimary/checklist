import os
import json
import logging
import argparse
import re
import time
from tqdm import tqdm
from pathlib import Path
from datetime import datetime
# from colorama import Fore, Style

from checklist.database_utils.db_info import get_db_schema_from_json
from checklist.red.parser.schema import Schema
from checklist.spinner import Spinner
from checklist.test_types import SEM, DIF, EXP, MTP, ORC
from evaluation.bird_evaluation.evaluation import execute_model
from judges import LLMJudge, GuardianJudge
from evalution import run_evalution, run_nl2sql_bugs_evalution

TEST_CLASS_MAP = {
    "sem": SEM,
    "orc": ORC,
    "mtp": MTP,
    "dif": DIF,
    "exp": EXP
}

if __name__ == '__main__':
    args_parser = argparse.ArgumentParser()
    args_parser.add_argument('--judge_name', type=str, required=True, default='')
    args_parser.add_argument('--enable_few_shots', action='store_true')
    args_parser.add_argument('--enable_cot', action='store_true')
    args_parser.add_argument('--benchmark_name', type=str, required=True, default='')
    args_parser.add_argument('--db_root_path', type=str, required=True, default='')
    args_parser.add_argument('--data_file_path', type=str, required=True, default='')
    args_parser.add_argument('--schema_file_path', type=str, required=True, default='')
    args_parser.add_argument('--nl2sql_model_name', type=str)
    args_parser.add_argument('--predicted_sql_path', type=str)
    args_parser.add_argument('--evaluation_only', action='store_true')
    args_parser.add_argument('--append_mode', action='store_true')
    args_parser.add_argument('--debug', action='store_true')
    args = args_parser.parse_args()
    
    # prepare output file path
    output_file_dir = os.path.join(os.path.dirname(args.data_file_path), "results")
    # if not os.path.exists(output_file_dir):
    #     os.makedirs(output_file_dir)
    
    # load to-test data and predicted sqls (if applicable)
    data = json.load(open(args.data_file_path))
    if args.benchmark_name in ["spider", "bird"]:
        assert args.predicted_sql_path is not None
        preds = [line.strip().split("\t")[0] for line in open(args.predicted_sql_path).readlines()]
        assert len(preds) == len(data)
    
    # initialize the judge
    if "guardian" in args.judge_name.lower():
        match = re.search(r"gpt[-\w.]+", args.judge_name.lower())
        backbone = match.group() if match else "gpt-4o-mini-0708"
        tests = [cls for key, cls in TEST_CLASS_MAP.items() if key in args.judge_name.lower()]
        if not tests:
            raise ValueError(f"No matching test class found for '{args.judge_name}'")
        judge = GuardianJudge(backbone, *tests)
    else:
        judge = LLMJudge(model_name=args.judge_name, 
                         enable_few_shot=True if args.enable_few_shots else False,
                         enable_cot=True if args.enable_cot else False,
                         )

    output_file_name = (
        f"judgments,dataset={args.benchmark_name}"
        f"{f'+{args.nl2sql_model_name}' if hasattr(args, 'nl2sql_model_name') and args.nl2sql_model_name else ''},"
        f"judge={args.judge_name}"
        f"{'+3-shots' if getattr(args, 'enable_few_shots', False) else ''}"
        f"{'+cot' if getattr(args, 'enable_cot', False) else ''}.jsonl"
    )
    output_file_path = os.path.join(output_file_dir, output_file_name)
    start_idx = 0
    if os.path.exists(output_file_path):
        with open(output_file_path, "r") as f:
            start_idx = sum(1 for _ in f)
    if not args.debug:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file_path = f'logs/{timestamp}_{Path(output_file_name).stem}.log'
        logging.basicConfig(
            level=logging.INFO,               # 日志最低级别
            filename=log_file_path,               # 日志文件名
            filemode='a',                     # 'a' 追加模式, 'w' 会覆盖
            format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",  # 日志格式
            force=True
        )
    else: logging.basicConfig(level=logging.INFO, force=True)

    if args.append_mode: data = data[start_idx:]
    judgments = open(output_file_path, 'a+')
    
    if not args.evaluation_only:
        total_time = 0
        red_schemas = {}
        for idx, ex in tqdm(enumerate(data), total=len(data), bar_format="{l_bar}{bar:10}{r_bar}{bar:-10b}"):
            # if idx < 395: continue
            db_id = ex['db_id']
            db_path = os.path.join(args.db_root_path, db_id, f"{db_id}.sqlite")
            nl = ex['question']
            if "guardian" in args.judge_name.lower() and db_id not in red_schemas.keys():
                spinner = Spinner(f"RED reading schema for DB `{db_id}` ...")
                with spinner:
                    red_schemas[db_id] = Schema(get_db_schema_from_json(db_id, args.schema_file_path), db_path)
            # Based on the benchmark, we may have different keys for: 1) hint information 2) predicted SQL 3) gold SQL
            hint = ex['evidence'] if 'evidence' in ex else ""
            pred = preds[idx+start_idx] if args.benchmark_name in ["spider", "bird"] else ex['sql']
            ret = {}
            if args.benchmark_name in ["spider", "bird"]:
                # for spider and bird, we need to execute the predicted SQL and compare with the gold SQL to determine correctness
                gold_sql = ex['SQL'] if 'SQL' in ex else ex['query']
                ret = execute_model(pred, gold_sql, db_path, idx=-1, meta_time_out=30.0)
            else:
                # for nl2sql-bugs, we directly use the label in the dataset
                ret['res'] = 1 if ex['label'] == True else 0
            judgment_label = True if ret['res'] == 1 else False
            
            logging.info("=" * 80)
            logging.debug(f"NL query: {nl}\nPredicted SQL: {pred}{f'Gold SQL: {gold_sql}' if args.benchmark_name in ['spider', 'bird'] else ''}")
            print(f"\033[94m\nNatural Language: \033[92m{nl}\033[0m")
            print(f"\033[94mPredicted: \033[92m{pred}\033[0m")
            if args.benchmark_name in ["spider", "bird"]:
                print(f"\033[94mGold: \033[91m{gold_sql}\033[0m")

            start=time.time()
            # run the judge
            judge.set(nl=nl, hint=hint, pred=pred, db_id=db_id, db_root_path=args.db_root_path, schema_file_path=args.schema_file_path, 
                      red_schema=red_schemas[db_id] if "guardian" in args.judge_name.lower() else None, pred_match_gold=judgment_label)
            ret = judge.run()
            logging.info(f"Result: {ret}")
            # judge.summary()
            json.dump(ret, judgments)
            judgments.write('\n')
            judgments.flush()
            total_time+=time.time()-start
        print(f"Test avg execution time <{args.judge_name}({args.benchmark_name})>: {(total_time)/len(data):.4f} seconds ({total_time}/{len(data)})")

    run_evalution(judge_name=args.judge_name, benchmark_name=args.benchmark_name,
                  judgment_file_path=output_file_path, data_file_path=args.data_file_path, 
                  db_root_path=args.db_root_path, predicted_sql_path=args.predicted_sql_path)
    # if not args.benchmark_name in ["spider", "bird"]:
    #     run_nl2sql_bugs_evalution(
    #         judge_name=args.judge_name, judgment_file_path=output_file_path, data_file_path=args.data_file_path, db_root_path=args.db_root_path)
    # print(f"Log is saved to {log_file_path}\nJudgment results saved to {output_file_path}")