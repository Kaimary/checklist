import os
import json
import argparse

from evalution import run_evalution, run_nl2sql_bugs_evalution

def check_model_outputs_consistency(esemble_mode, sem_output_file_path, orc_output_file_path, mtp_output_file_path, dif_output_file_path, exp_output_file_path):
    if "sem" in esemble_mode and not os.path.exists(sem_output_file_path):
        raise ValueError(f"Expected SEM judgments file (`{sem_output_file_path}`) not found!")
    if "orc" in esemble_mode and not os.path.exists(orc_output_file_path):
        raise ValueError(f"Expected ORC judgments file (`{orc_output_file_path}`) not found!")
    if "mtp" in esemble_mode and not os.path.exists(mtp_output_file_path):
        raise ValueError(f"Expected MTP judgments file (`{mtp_output_file_path}`) not found!")
    if "dif" in esemble_mode and not os.path.exists(dif_output_file_path):
        raise ValueError(f"Expected DIF judgments file (`{dif_output_file_path}`) not found!")
    if "exp" in esemble_mode and not os.path.exists(exp_output_file_path):
        raise ValueError(f"Expected EXP judgments file (`{exp_output_file_path}`) not found!")

def confidence_based_vote(orc, sem, mtp, dif, exp):
    """
    基于方法可靠性进行投票
    """
    # 置信度分数
    confidence_scores = {
        'dif': 0.9,    # 高置信度
        'exp': 0.8,    # 中高置信度
        'sem': 0.7,    # 中等置信度
        'mtp': 0.5,    # 低置信度（因为有未确定）
        'orc': 0.4     # 低置信度（因为有未确定）
    }
    
    total_score = 0
    max_score = 0
    
    for method, result in [('dif', dif), ('exp', exp), ('sem', sem), 
                          ('mtp', mtp), ('orc', orc)]:
        weight = confidence_scores[method]
        max_score += weight
        
        if result == True:
            total_score += weight
        elif result == False:
            total_score += 0
        else:  # Undetermined
            total_score += weight * 0.2  # 给予很低的分值
    
    # 需要超过60%的置信度
    return (total_score / max_score) >= 0.6

def hierarchical_undetermined(orc, sem, mtp, dif, exp):
    """
    层级决策，明确处理未确定状态
    """
    # 第一层：高置信度组合（不考虑未确定）
    if dif and exp:
        return True
    
    # 第二层：dif主导，需要明确支持
    if dif:
        # 需要至少一个明确的True支持（排除Undetermined）
        supporting_evidence = 0
        if sem: supporting_evidence += 1  # sem没有未确定
        if exp: supporting_evidence += 1  # exp没有未确定  
        if orc == True: supporting_evidence += 1  # 只有明确True
        if mtp == True: supporting_evidence += 1  # 只有明确True
        
        if supporting_evidence >= 1:
            return True
        else:
            return False
    
    # 第三层：没有dif，需要强一致性
    if exp:
        # 需要明确的多数支持
        true_count = 0
        if sem: true_count += 1
        if orc == True: true_count += 1
        if mtp == True: true_count += 1
        
        if true_count >= 2:  # 需要至少2个明确支持
            return True
    
    return False

def majority_privilege(orc, sem, mtp, dif, exp):
    """多数投票，但给最佳方法特权"""
    votes = [orc, sem, mtp, dif, exp]
    true_count = sum(votes)
    
    # 基本多数投票
    if true_count >= 3:  # 至少3个通过
        return True
    if true_count <= 1:  # 最多1个通过
        return False
    
    # 平局情况（2个通过）：依赖最佳方法
    if dif:  # 最佳方法有决定权
        return True
    if not exp:  # 次佳方法反对
        return False
        
    return False  # 默认

def rule_engine(orc, sem, mtp, dif, exp):
    """基于性能统计的规则引擎"""
    # 规则1：高精确率方法优先
    if dif:  # PP=0.894，很高
        return True
        
    # 规则2：高召回率方法补偿
    if sem and (exp or mtp):  # sem的PR=0.989，用其他方法验证
        return True
        
    # 规则3：避免低精确率方法主导
    if orc and not (dif or exp):  # orc的PP较低
        return False
        
    # 规则4：中等性能方法组合
    if (exp and mtp) or (exp and sem):
        return True
        
    return False

def precision_first(orc, sem, mtp, dif, exp):
    """
    以dif为核心，其他方法作为验证
    """
    # 规则1：如果dif通过，需要至少1个其他高精确率方法支持
    if dif:
        if exp or orc:  # exp和orc都有相对较高的精确率
            return True
        else:
            return False  # dif单独通过可能不可靠
    
    # 规则2：如果dif不通过，需要强证据才能推翻
    else:
        if (exp and sem and mtp) or (exp and orc and sem):
            return True
        else:
            return False

# def dynamic_threshold(orc, sem, mtp, dif, exp):
#     """
#     根据方法组合动态调整阈值
#     """
#     true_count = sum([orc, sem, mtp, dif, exp])
    
#     # 高精确率组合：直接通过
#     if dif and exp and orc:  # 三个高PP方法
#         return True
#     if dif and exp and (sem or mtp):  # 两个高PP + 一个中等
#         return True
    
#     # 中等组合：需要更多证据
#     if true_count >= 4:  # 4个以上方法同意
#         return True
#     if true_count == 3 and dif:  # 3个同意且包含dif
#         return True
    
#     # 低置信度组合：拒绝
#     return False

def dynamic_threshold(orc, sem, mtp, dif, exp):
    """
    根据方法组合动态调整阈值，处理未确定状态
    """
    # 处理未确定状态：只有明确True才计数
    def is_true(value):
        return value == True  # 明确为True，排除False和Undetermined
    
    # 计算明确True的数量
    true_count = sum([is_true(orc), is_true(sem), is_true(mtp), is_true(dif), is_true(exp)])
    
    # 高精确率组合：直接通过（要求明确True）
    if is_true(dif) and is_true(exp) and is_true(orc):  # 三个高PP方法都明确通过
        return True
    if is_true(dif) and is_true(exp) and (is_true(sem) or is_true(mtp)):  # 两个高PP + 一个中等明确通过
        return True
    
    # 中等组合：需要更多证据
    if true_count >= 4:  # 4个以上方法明确同意
        return True
    if true_count == 3 and is_true(dif):  # 3个明确同意且包含dif
        return True
    
    # 特殊处理：dif明确通过，但其他方法有未确定的情况
    if is_true(dif):
        # dif明确通过，检查是否有足够的支持证据
        supporting_evidence = 0
        if is_true(exp): supporting_evidence += 2  # exp权重高
        if is_true(sem): supporting_evidence += 1
        if is_true(mtp): supporting_evidence += 1
        if is_true(orc): supporting_evidence += 1
        
        # 处理未确定状态的潜在支持
        if orc is None: supporting_evidence += 0.3  # orc未确定给予部分支持
        if mtp is None: supporting_evidence += 0.3  # mtp未确定给予部分支持
        
        if supporting_evidence >= 1.5:  # 调整阈值
            return True
    
    # 特殊情况：没有dif但其他方法强烈一致
    if not is_true(dif) and true_count >= 4:  # 4个方法明确同意（不含dif）
        return True
    
    # 低置信度组合：拒绝
    return False

def matrix_based_rules(orc, sem, mtp, dif, exp):
    """
    基于各方法的错误模式设计规则
    """
    true_count = sum([orc, sem, mtp, dif, exp])
    false_count = 5 - true_count
    
    # 避免sem和mtp的高FP问题
    if sem and mtp and not dif:  # 两个高FP方法一致但dif反对
        return False
    
    # 利用orc的高精确率但低召回率特性
    if orc and not dif:  # orc通过但dif不通过，可能误报
        return False
    
    # 核心规则：以dif为主，exp为辅
    if dif:
        if exp or (orc and sem):  # dif + exp 或 dif + orc + sem
            return True
        else:
            return False
    else:
        if exp and sem and mtp and orc:  # 所有其他方法一致
            return True
        else:
            return False

def conservative_ensemble(orc, sem, mtp, dif, exp):
    """
    将Undetermined视为False，保守策略
    """
    # 将Undetermined视为不支持
    orc_valid = (orc == True)  # 只有明确True才计数
    mtp_valid = (mtp == True)  # 只有明确True才计数
    
    # 高置信度规则
    if dif and exp:  # 两个最可靠的方法一致
        return True
    
    # 中等置信度：dif + 其他验证
    if dif and (orc_valid or mtp_valid or sem):
        return True
        
    # 强一致性：多个方法一致支持
    if exp and sem and (orc_valid or mtp_valid):
        return True
        
    return False

def weighted_undetermined(orc, sem, mtp, dif, exp):
    """
    给未确定状态分配部分权重
    """
    weights = {
        'dif': 0.30, 'exp': 0.25, 'sem': 0.20, 
        'mtp': 0.10, 'orc': 0.08  # 降低不确定方法的权重
    }
    
    # Undetermined给予部分分数
    def get_score(method, result):
        if result == True:
            return weights[method]
        elif result == False:
            return 0
        else:  # Undetermined
            return weights[method] * 0.3  # 30%的置信度
    
    total_score = (get_score('dif', dif) + get_score('exp', exp) + 
                   get_score('sem', sem) + get_score('mtp', mtp) + 
                   get_score('orc', orc))
    
    return total_score >= 0.35  # 调整阈值

def dynamic_threshold_conservative(orc, sem, mtp, dif, exp):
    """
    保守版本：只考虑明确True，忽略未确定状态
    """
    # 只计算明确为True的方法
    def count_clear_true(*values):
        return sum(1 for value in values if value == True)
    
    clear_true_count = count_clear_true(orc, sem, mtp, dif, exp)
    
    # 规则1：必须有dif明确通过
    if dif != True:  # dif不明确通过
        return False
    
    # 规则2：dif明确通过的情况下，需要足够的明确支持
    if dif == True:
        supporting_clear = count_clear_true(exp, sem, mtp, orc)
        
        # 高置信度：dif + exp明确通过
        if exp == True:
            return True
        # 中等置信度：dif + 至少2个其他方法明确通过
        elif supporting_clear >= 2:
            return True
        # 低置信度：dif + 1个其他方法明确通过 + 没有明确反对
        elif supporting_clear >= 1 and not any(val == False for val in [exp, sem, mtp, orc]):
            return True
    
    return False

if __name__ == '__main__':
    args_parser = argparse.ArgumentParser()
    args_parser.add_argument('--esemble_mode', type=str, required=True, default='')
    args_parser.add_argument('--benchmark_name', type=str, required=True, default='')
    args_parser.add_argument('--db_root_path', type=str, required=True, default='')
    args_parser.add_argument('--data_file_path', type=str, required=True, default='')
    args_parser.add_argument('--schema_file_path', type=str, required=True, default='')
    args_parser.add_argument('--nl2sql_model_name', type=str)
    args_parser.add_argument('--predicted_sql_path', type=str)
    args = args_parser.parse_args()
    
    # prepare output file path
    output_file_dir = os.path.join(os.path.dirname(args.data_file_path), "results")
    
    # load to-test data and predicted sqls (if applicable)
    data = json.load(open(args.data_file_path))
    if args.benchmark_name in ["spider", "bird"]:
        assert args.predicted_sql_path is not None
        preds = [line.strip() for line in open(args.predicted_sql_path).readlines()]
        assert len(preds) == len(data)

    base_output_file_name = (f"judgments,dataset={args.benchmark_name}"
                   f"{f'+{args.nl2sql_model_name}' if hasattr(args, 'nl2sql_model_name') and args.nl2sql_model_name else ''},judge=")
    sem_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(sem).jsonl")
    sem_judgments = [json.loads(line) for line in open(sem_output_file_path)] if os.path.exists(sem_output_file_path) else []
    orc_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(orc).jsonl")
    orc_judgments = [json.loads(line) for line in open(orc_output_file_path)] if os.path.exists(orc_output_file_path) else []
    mtp_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(mtp).jsonl")
    mtp_judgments = [json.loads(line) for line in open(mtp_output_file_path)] if os.path.exists(mtp_output_file_path) else []
    dif_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(dif).jsonl")
    dif_judgments = [json.loads(line) for line in open(dif_output_file_path)] if os.path.exists(dif_output_file_path) else []
    exp_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(exp).jsonl")
    exp_judgments = [json.loads(line) for line in open(exp_output_file_path)] if os.path.exists(exp_output_file_path) else []
    check_model_outputs_consistency(
        args.esemble_mode, sem_output_file_path, orc_output_file_path, mtp_output_file_path, dif_output_file_path, exp_output_file_path)
    
    esemble_output_file_path = os.path.join(output_file_dir, base_output_file_name + f"guardian({args.esemble_mode}).jsonl")
    # judgments = open(esemble_output_file_path, 'w')
    judgments = []
    for i in range(len(data)):
        # judgment = dynamic_threshold(orc_judgments[i]["judgment"], sem_judgments[i]["judgment"], mtp_judgments[i]["judgment"], dif_judgments[i]["judgment"], exp_judgments[i]["judgment"])
        judgment = dynamic_threshold_conservative(orc_judgments[i]["judgment"], sem_judgments[i]["judgment"], mtp_judgments[i]["judgment"], dif_judgments[i]["judgment"], exp_judgments[i]["judgment"])
        judgments.append({
            **({"SEM Test": sem_judgments[i]["judgment"]} if "sem" in args.esemble_mode else {}),
            **({"ORC Test": orc_judgments[i]["judgment"]} if "orc" in args.esemble_mode else {}),
            **({"MTP Test": mtp_judgments[i]["judgment"]} if "mtp" in args.esemble_mode else {}),
            **({"DIF Test": dif_judgments[i]["judgment"]} if "dif" in args.esemble_mode else {}),
            **({"EXP Test": exp_judgments[i]["judgment"]} if "exp" in args.esemble_mode else {}),
            "judgment": judgment
        })
        # # critical condition (if sem test to be false, then final judgment to be false)
        # if "sem" in args.esemble_mode and not sem_judgments[i]["judgment"] and "mtp" in args.esemble_mode and not mtp_judgments[i]["judgment"]:
        #     judgments.append({
        #         **({"SEM Test": sem_judgments[i]["judgment"]} if "sem" in args.esemble_mode else {}),
        #         **({"ORC Test": orc_judgments[i]["judgment"]} if "orc" in args.esemble_mode else {}),
        #         **({"MTP Test": mtp_judgments[i]["judgment"]} if "mtp" in args.esemble_mode else {}),
        #         **({"DIF Test": dif_judgments[i]["judgment"]} if "dif" in args.esemble_mode else {}),
        #         **({"EXP Test": exp_judgments[i]["judgment"]} if "exp" in args.esemble_mode else {}),
        #         "judgment": False
        #     })
        #     continue
        # if "exp" in args.esemble_mode and not exp_judgments[i]["judgment"] and "dif" in args.esemble_mode and not dif_judgments[i]["judgment"]:
        #     judgments.append({
        #         **({"SEM Test": sem_judgments[i]["judgment"]} if "sem" in args.esemble_mode else {}),
        #         **({"ORC Test": orc_judgments[i]["judgment"]} if "orc" in args.esemble_mode else {}),
        #         **({"MTP Test": mtp_judgments[i]["judgment"]} if "mtp" in args.esemble_mode else {}),
        #         **({"DIF Test": dif_judgments[i]["judgment"]} if "dif" in args.esemble_mode else {}),
        #         **({"EXP Test": exp_judgments[i]["judgment"]} if "exp" in args.esemble_mode else {}),
        #         "judgment": False
        #     })
        #     continue
        # score = 0
        # if "sem" in args.esemble_mode: 
        #     score += 1 if sem_judgments[i]["judgment"] else -2
        # if "mtp" in args.esemble_mode: 
        #     score += 1 if mtp_judgments[i]["judgment"] else -2
        # if "dif" in args.esemble_mode:
        #     score += 1 if dif_judgments[i]["judgment"] else -1
        # # early stop
        # # if score >= 0.5 or score <= -0.5:
        # #     judgments.append({"judgment": True}) if score >= 0.5 else judgments.append({"judgment": False})
        # #     continue
        # if "orc" in args.esemble_mode:
        #     score += 2 if orc_judgments[i]["judgment"] else -1
        # # early stop
        # # if score >= 0.5 or score <= -0.5:
        # #     judgments.append({"judgment": True}) if score >= 0.5 else judgments.append({"judgment": False})
        # #     continue
        # if "exp" in args.esemble_mode:
        #     score += 1 if exp_judgments[i]["judgment"] else -2
        # judgments.append({
        #         **({"SEM Test": sem_judgments[i]["judgment"]} if "sem" in args.esemble_mode else {}),
        #         **({"ORC Test": orc_judgments[i]["judgment"]} if "orc" in args.esemble_mode else {}),
        #         **({"MTP Test": mtp_judgments[i]["judgment"]} if "mtp" in args.esemble_mode else {}),
        #         **({"DIF Test": dif_judgments[i]["judgment"]} if "dif" in args.esemble_mode else {}),
        #         **({"EXP Test": exp_judgments[i]["judgment"]} if "exp" in args.esemble_mode else {}),
        #         "judgment": True
        #     }) if score >= 0 else judgments.append({
        #         **({"SEM Test": sem_judgments[i]["judgment"]} if "sem" in args.esemble_mode else {}),
        #         **({"ORC Test": orc_judgments[i]["judgment"]} if "orc" in args.esemble_mode else {}),
        #         **({"MTP Test": mtp_judgments[i]["judgment"]} if "mtp" in args.esemble_mode else {}),
        #         **({"DIF Test": dif_judgments[i]["judgment"]} if "dif" in args.esemble_mode else {}),
        #         **({"EXP Test": exp_judgments[i]["judgment"]} if "exp" in args.esemble_mode else {}),
        #         "judgment": False
        #     })
    
    with open(esemble_output_file_path, 'w') as f:
        for item in judgments:
            f.write(json.dumps(item) + '\n')

    run_evalution(judge_name=f"guardian({args.esemble_mode})", benchmark_name=args.benchmark_name,
                  judgment_file_path=esemble_output_file_path, data_file_path=args.data_file_path, 
                  db_root_path=args.db_root_path, predicted_sql_path=args.predicted_sql_path)
    if not args.benchmark_name in ["spider", "bird"]:
        run_nl2sql_bugs_evalution(judge_name=f"guardian({args.esemble_mode})", 
                                  judgment_file_path=esemble_output_file_path, 
                                  data_file_path=args.data_file_path, db_root_path=args.db_root_path)