import json
import argparse
import os
from tqdm import tqdm

from evaluation.bird_evaluation.evaluation import execute_model


def run_evalution(judge_name, judgment_file_path, benchmark_name, db_root_path, data_file_path, nl2sql_model_name=None, predicted_sql_path=None):    
    data = json.load(open(data_file_path))
    judgments = [json.loads(line) for line in open(judgment_file_path)]
    if benchmark_name in ["spider", "bird"]:
        assert predicted_sql_path is not None
        preds = [value.split('\t-----')[0] for value in json.load(open(predicted_sql_path)).values()] \
            if benchmark_name == "bird" else [line.strip() for line in open(predicted_sql_path).readlines()]
        assert len(preds) == len(data)

    Acc = 0
    simple_acc = 0
    simples = 0
    moderate_acc = 0
    moderates = 0
    challenging_acc = 0
    challengings = 0
    TP  = 0  # correct SQL and testing judgment passed
    FN  = 0  # correct SQL but testing judgment failed
    FP  = 0  # incorrect SQL but testing judgment falsely passsed
    TN  = 0  # incorrect SQL and testing judgment failed    
    for idx, (ex, judgment) in tqdm(enumerate(zip(data[:len(judgments)], judgments)), total=len(data)):
        # print(idx)
        db_id = ex['db_id']
        db_path = os.path.join(db_root_path, db_id, f"{db_id}.sqlite")
        pred = preds[idx] if benchmark_name in ["spider", "bird"] else ex['sql']
        difficulty = ex['difficulty'] if 'difficulty' in ex else 'unknown'
        if difficulty == 'simple': simples += 1
        elif difficulty == 'moderate': moderates += 1
        elif difficulty == 'challenging': challengings += 1

        ret = {}
        if benchmark_name in ["spider", "bird"]:
            # for spider and bird, we need to execute the predicted SQL and compare with the gold SQL to determine correctness
            gold_sql = ex['SQL'] if 'SQL' in ex else ex['query']
            ret = execute_model(pred, gold_sql, db_path, idx=-1, meta_time_out=30.0)
        else:
            # for nl2sql-bugs, we directly use the label in the dataset
            ret['res'] = 1 if ex['label'] == True else 0
        judgment_label = True if ret['res'] == 1 else False
        if 'judgment' not in judgment:
            print(f"Warning: No judgment found for index {idx}. Skipping this entry.")
            continue
        if judgment_label == judgment['judgment']: 
            Acc += 1
            if difficulty == 'simple': simple_acc += 1
            elif difficulty == 'moderate': moderate_acc += 1
            elif difficulty == 'challenging': challenging_acc += 1

        if judgment['judgment'] and judgment_label == judgment['judgment']: TP += 1
        elif not judgment['judgment'] and judgment_label != judgment['judgment']: 
            # print(idx)
            FN += 1
        elif judgment['judgment'] and judgment_label != judgment['judgment']: FP += 1
        else: TN += 1
    
    print(f"Evaluation Results of `{judge_name}` on `{benchmark_name}{f'+{nl2sql_model_name}' if nl2sql_model_name else ''}`:")
    print(f"Total Accuracy: {Acc/len(judgments)}")
    if simples > 0:
        print(f"Simple-Difficulty Accuracy: {simple_acc/simples} ({simple_acc}/{simples})")
        print(f"Moderate-Difficulty Accuracy: {moderate_acc/moderates} ({moderate_acc}/{moderates})")
        print(f"Challenging-Difficulty Accuracy: {challenging_acc/challengings} ({challenging_acc}/{challengings})")
    print(f"TP: {TP}, FN: {FN}, FP: {FP}, TN: {TN}")
    positive_precision = TP / (TP + FP) if TP + FP > 0 else 0
    positive_recall = TP / (TP + FN) if TP + FN > 0 else 0
    negative_precision = TN / (TN + FN) if TN + FN > 0 else 0
    negative_recall = TN / (TN + FP) if TN + FP > 0 else 0
    f1 = 2 * (positive_precision * positive_recall) / (positive_precision + positive_recall) if positive_precision + positive_recall > 0 else 0
    print(f"PP: {positive_precision}, PR: {positive_recall}, NP: {negative_precision}, NR: {negative_recall}, F1: {f1}")