import os
import sys
import time
import json
import logging
import argparse
from tqdm import tqdm
from pathlib import Path
from datetime import datetime

from evalution import run_evalution, run_nl2sql_bugs_evalution
from checklist.utils import createJudge, get_data_from_bench, get_red_schemas, print_summary

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
    # prepare output file
    output_file_dir = os.path.join(os.path.dirname(args.data_file_path), "results")
    nl2sql = getattr(args, 'nl2sql_model_name', None)
    output_file_name = (
        f"judgments,dataset={args.benchmark_name}"
        f"{f'+{nl2sql}' if nl2sql else ''},"
        f"judge={args.judge_name}"
        f"{'+3-shots' if args.enable_few_shots else ''}"
        f"{'+cot' if args.enable_cot else ''}.jsonl"
    )
    output_file_path = os.path.join(output_file_dir, output_file_name)
    logging.basicConfig(
        level=logging.INFO,
        filename=(
            f'logs/{datetime.now():%Y%m%d_%H%M%S}_{Path(output_file_name).stem}.log'
            if not args.debug else None
        ),
        filemode='a',
        format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
        force=True,
    )
    # load to-test data
    data = json.load(open(args.data_file_path))
    # initialize the judge
    judge = createJudge(args.judge_name, enable_few_shots=args.enable_few_shots, enable_cot =args.enable_cot)
    start_idx = 0
    if args.append_mode and os.path.exists(output_file_path):
        start_idx = sum(1 for _ in open(output_file_path))
        data = data[start_idx:]
    if args.evaluation_only:
        run_evalution(judge_name=args.judge_name, benchmark_name=args.benchmark_name,
                      judgment_file_path=output_file_path, data_file_path=args.data_file_path, 
                      db_root_path=args.db_root_path, predicted_sql_path=args.predicted_sql_path)
        sys.exit(0)
    
    time_elapse = 0
    judgments = open(output_file_path, 'a+')
    red_schemas = get_red_schemas(data, args.db_root_path, args.schema_file_path)
    for idx, ex in tqdm(enumerate(data), total=len(data), bar_format="{l_bar}{bar:10}{r_bar}{bar:-10b}"):
        # if idx < 395: continue
        db_id, db_path, nl, hint, pred, gold, judgment_gold_label = get_data_from_bench(
            ex, idx+start_idx, args.benchmark_name, args.predicted_sql_path, args.db_root_path)
        
        # debugging info
        logging.debug(f"NL query: {nl}\nPredicted SQL: {pred}\nGold: {gold}")
        print(f"\033[94m\nNatural Language: \033[92m{nl}\033[0m")
        print(f"\033[94mPredicted: \033[92m{pred}\033[0m")
        if gold: print(f"\033[94mGold: \033[91m{gold}\033[0m")

        start=time.time()
        judge.set(nl, hint, pred, db_id, db_root_path=args.db_root_path, red_schema=red_schemas[db_id])
        # run the judge
        ret = judge.run()
        # print out judgment summary if not beat baseline/ground-truth
        print_summary(judge, ret, idx+start_idx, judgment_gold_label, output_file_name, output_file_dir)
        # update output
        json.dump(ret, judgments)
        judgments.write('\n')
        judgments.flush()
        time_elapse+=time.time()-start
    print(f"Test avg execution time <{args.judge_name}({args.benchmark_name})>: {(time_elapse)/len(data):.4f} seconds ({time_elapse}/{len(data)})")

    run_evalution(judge_name=args.judge_name, benchmark_name=args.benchmark_name,
                  judgment_file_path=output_file_path, data_file_path=args.data_file_path, 
                  db_root_path=args.db_root_path, predicted_sql_path=args.predicted_sql_path)
    # if not args.benchmark_name in ["spider", "bird"]:
    #     run_nl2sql_bugs_evalution(
    #         judge_name=args.judge_name, judgment_file_path=output_file_path, data_file_path=args.data_file_path)
    # print(f"Log is saved to {log_file_path}\nJudgment results saved to {output_file_path}")