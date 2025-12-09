import os
import json
import argparse
import numpy as np

from evalution import run_evalution, run_nl2sql_bugs_evalution

def check_model_outputs_consistency(esemble_mode, sem, orc, srt, cmt, nrt):
    if "sem" in esemble_mode and not os.path.exists(sem):
        raise ValueError(f"Expected SEM judgments file (`{sem}`) not found!")
    if "orc" in esemble_mode and not os.path.exists(orc):
        raise ValueError(f"Expected ORC judgments file (`{orc}`) not found!")
    if "mtp" in esemble_mode and not os.path.exists(srt):
        raise ValueError(f"Expected MTP judgments file (`{srt}`) not found!")
    if "dif" in esemble_mode and not os.path.exists(cmt):
        raise ValueError(f"Expected DIF judgments file (`{cmt}`) not found!")
    if "exp" in esemble_mode and not os.path.exists(nrt):
        raise ValueError(f"Expected EXP judgments file (`{nrt}`) not found!")

def bayesian_fusion_with_undetermined(test_results, prior_prob=0.5):
    """
    处理UNDETERMINED结果的贝叶斯融合方案
    """
    
    test_metrics = {
        'mst': {'acc': 0.5050, 'pp': 0.5050, 'pr': 1.0,    'np': 0,      'nr': 0},
        'sem': {'acc': 0.5887, 'pp': 0.5517, 'pr': 0.9892, 'np': 0.9424, 'nr': 0.1802},
        'orc': {'acc': 0.5763, 'pp': 0.8091, 'pr': 0.2539, 'np': 0.5303, 'nr': 0.9336},
        'srt': {'acc': 0.5092, 'pp': 0.5080, 'pr': 0.9704, 'np': 0.5455, 'nr': 0.0364},
        'sst': {'acc': 0.5099, 'pp': 0.4912, 'pr': 0.9799, 'np': 0.8286, 'nr': 0.0873},
        'cmt': {'acc': 0.8781, 'pp': 0.8786, 'pr': 0.8803, 'np': 0.8776, 'nr': 0.8759},
        'nrt': {'acc': 0.5556, 'pp': 0.5353, 'pr': 0.9087, 'np': 0.6771, 'nr': 0.1952},
        'qrt': {'acc': 0.5922, 'pp': 0.5557, 'pr': 0.9598, 'np': 0.8411, 'nr': 0.2172}
    }
    
    # 计算每个测试方法的似然比（包含不确定情况的处理）
    likelihood_ratios = {}
    uncertainty_weights = {}
    
    for test_name, metrics in test_metrics.items():
        # 当测试结果为True时的似然比
        p_true_given_correct = metrics['pr']
        p_true_given_incorrect = 1 - metrics['nr']
        
        if p_true_given_incorrect > 0:
            lr_true = p_true_given_correct / p_true_given_incorrect
        else:
            lr_true = 10.0
            
        # 当测试结果为False时的似然比
        p_false_given_correct = 1 - metrics['pr']
        p_false_given_incorrect = metrics['nr']
        
        if p_false_given_incorrect > 0:
            lr_false = p_false_given_correct / p_false_given_incorrect
        else:
            lr_false = 10.0
        
        likelihood_ratios[test_name] = {
            True: np.log(lr_true),
            False: np.log(lr_false),
            'UNDETERMINED': 0  # 不确定结果不提供信息，似然比为1（对数为0）
        }
        
        # 不确定性权重：基于测试方法的可靠性
        uncertainty_weights[test_name] = metrics['acc']  # 使用准确率作为可靠性指标
    
    # 计算后验概率的对数比值
    log_odds = np.log(prior_prob / (1 - prior_prob))
    total_effective_weight = 0
    
    for test_name, result in test_results.items():
        if result != 'UNDETERMINED':
            log_odds += likelihood_ratios[test_name][result]
            total_effective_weight += uncertainty_weights[test_name]
        else:
            # 不确定结果使用部分权重
            partial_weight = uncertainty_weights[test_name] * 0.3  # 不确定结果权重降低
            total_effective_weight += partial_weight
    
    # 如果没有有效测试结果，返回不确定
    if total_effective_weight == 0:
        return 'UNDETERMINED', 0.5, 0.5
    
    # 转换为概率
    posterior_prob = 1 / (1 + np.exp(-log_odds))
    
    # 基于有效权重调整置信度
    confidence_adjustment = min(1.0, total_effective_weight / 4.0)  # 假设最大有效权重为4
    adjusted_confidence = 0.5 + (posterior_prob - 0.5) * confidence_adjustment
    
    if adjusted_confidence >= 0.6:
        final_decision = True
    elif adjusted_confidence <= 0.4:
        final_decision = False
    else:
        final_decision = 'UNDETERMINED'
    
    return final_decision, adjusted_confidence, posterior_prob

def enhanced_strict_majority_vote(test_results):
    """
    增强的严格多数投票，进一步减少FP
    """
    definite_results = [r for r in test_results.values() if r != 'UNDETERMINED']
    
    # 优先处理cmt测试结果
    if 'cmt' in test_results and test_results['cmt'] != 'UNDETERMINED':
        cmt_result = test_results['cmt']
        
        # cmt说错误时，高度可信（减少FP的关键）
        if cmt_result == False:
            # 统计其他测试的支持情况
            supporting_false = 0
            for test, result in test_results.items():
                if test != 'cmt' and result != 'UNDETERMINED' and result == False:
                    supporting_false += 1
            
            if supporting_false >= 1:  # 至少有一个其他测试支持错误
                return False, 0.9
            else:
                return False, 0.8  # cmt单独判断错误也较可信
        
        # cmt说正确时，需要更多支持（防止FP）
        else:  # cmt_result == True
            supporting_true = 0
            opposing_false = 0
            for test, result in test_results.items():
                if test != 'cmt' and result != 'UNDETERMINED':
                    if result == True:
                        supporting_true += 1
                    else:
                        opposing_false += 1
            
            # 对于正确判断需要更严格的条件
            if opposing_false == 0 and supporting_true >= 2:  # 没有反对且至少2个支持
                return True, 0.85
            elif supporting_true >= opposing_false + 2:  # 明显多数支持
                return True, 0.8
            else:
                return 'UNDETERMINED', 0.6
    
    # 没有cmt时的严格投票
    if len(definite_results) < 4:
        return 'UNDETERMINED', 0.5
    
    positive_count = sum(definite_results)
    negative_count = len(definite_results) - positive_count
    
    # 更严格的阈值，特别针对正确判断
    if positive_count >= 5 and positive_count >= negative_count + 3:  # 强烈支持正确
        confidence = 0.7 + (positive_count - negative_count) * 0.05
        return True, min(0.95, confidence)
    elif negative_count >= 3:  # 对错误判断相对宽松（减少FP）
        confidence = 0.7 + negative_count * 0.08
        return False, min(0.95, confidence)
    elif positive_count >= 4 and positive_count >= negative_count + 2:  # 中等支持正确
        return True, 0.75
    else:
        return 'UNDETERMINED', 0.5

def enhanced_strict_majority_vote_ablation(test_results):
    """
    增强的严格多数投票（支持部分测试参与的消融实验）。
    自动根据参与测试数调整阈值，重点减少FP。
    """
    # 过滤掉未确定的测试结果
    definite_results = {k: v for k, v in test_results.items() if v != 'UNDETERMINED'}
    if not definite_results:
        return 'UNDETERMINED', 0.5

    num_tests = len(definite_results)
    positive_count = sum(r for r in definite_results.values() if r is True)
    negative_count = sum(r is False for r in definite_results.values())

    # =========================
    # 特殊优先逻辑：CMT（Cross Model Test）
    # =========================
    if 'cmt' in test_results and test_results['cmt'] != 'UNDETERMINED':
        cmt_result = test_results['cmt']

        if cmt_result is False:
            # 如果 cmt 判错，则权重高，其他测试仅用于支持
            supporting_false = sum(
                1 for t, r in definite_results.items()
                if t != 'cmt' and r is False
            )
            if supporting_false >= 1:
                return False, 0.9  # 有额外支持
            else:
                return False, 0.8  # cmt 单独否定仍然可信

        elif cmt_result is True:
            # 如果 cmt 说正确，需要更多支持（防止FP）
            supporting_true = sum(
                1 for t, r in definite_results.items()
                if t != 'cmt' and r is True
            )
            opposing_false = sum(
                1 for t, r in definite_results.items()
                if t != 'cmt' and r is False
            )

            # 动态比例判断（至少70%支持正确且无明显反对）
            total_others = max(1, len(definite_results) - 1)
            support_ratio = supporting_true / total_others

            if opposing_false == 0 and support_ratio >= 0.7:
                return True, 0.85
            elif support_ratio >= 0.6 and supporting_true > opposing_false:
                return True, 0.8
            else:
                return 'UNDETERMINED', 0.6

    # =========================
    # 一般多数投票逻辑（无 cmt 或 cmt 未定）
    # =========================
    if num_tests < 2:
        # 样本太少，无法可靠判断
        return 'UNDETERMINED', 0.5

    pos_ratio = positive_count / num_tests
    neg_ratio = negative_count / num_tests

    # 更严格的正确判断门槛
    if pos_ratio >= 0.75 and positive_count >= negative_count + 1:
        confidence = 0.75 + (pos_ratio - 0.5) * 0.4  # 动态增强置信度
        return True, min(0.95, confidence)

    # 对错误判断较宽松（减少FP）
    elif neg_ratio >= 0.5:
        confidence = 0.7 + (neg_ratio - 0.3) * 0.5
        return False, min(0.95, confidence)

    # 其余情况无法确定
    return 'UNDETERMINED', 0.5

# def enhanced_strict_majority_vote_ablation(test_results):
#     """
#     修复版的严格多数投票（消融实验专用）
#     重点解决过于保守导致准确率低的问题
#     """
#     # 过滤掉未确定的测试结果
#     definite_results = {k: v for k, v in test_results.items() if v != 'UNDETERMINED'}
#     if not definite_results:
#         return 'UNDETERMINED', 0.5

#     num_tests = len(definite_results)
#     positive_count = sum(1 for r in definite_results.values() if r is True)
#     negative_count = num_tests - positive_count

#     # =========================
#     # 修复1：更平衡的CMT优先逻辑
#     # =========================
#     if 'cmt' in test_results and test_results['cmt'] != 'UNDETERMINED':
#         cmt_result = test_results['cmt']
        
#         # 统计其他测试结果（排除cmt）
#         other_results = {k: v for k, v in definite_results.items() if k != 'cmt'}
#         other_positive = sum(1 for r in other_results.values() if r is True)
#         other_negative = len(other_results) - other_positive
        
#         if cmt_result is False:
#             # 修复2：cmt判错时也需要其他测试支持，避免过度否定
#             if other_negative >= 1 or len(other_results) == 0:
#                 confidence = 0.7 + min(0.2, other_negative * 0.1)
#                 return False, confidence
#             else:
#                 # 如果其他测试都支持正确，转为不确定
#                 return 'UNDETERMINED', 0.6
                
#         elif cmt_result is True:
#             # 修复3：cmt判对时适当放宽条件
#             if other_negative == 0:  # 没有反对票
#                 confidence = 0.8 + min(0.15, other_positive * 0.05)
#                 return True, confidence
#             elif other_positive > other_negative:  # 支持票多于反对票
#                 confidence = 0.7
#                 return True, confidence
#             else:
#                 return 'UNDETERMINED', 0.6

#     # =========================
#     # 修复4：更宽松的一般投票逻辑
#     # =========================
#     # 根据测试数量动态调整阈值
#     if num_tests >= 3:
#         pos_ratio = positive_count / num_tests
#         neg_ratio = negative_count / num_tests
        
#         # 修复5：降低正确判断的门槛
#         if pos_ratio >= 0.6:  # 从0.75降到0.6
#             confidence = 0.6 + (pos_ratio - 0.6) * 0.8
#             return True, min(0.9, confidence)
            
#         # 修复6：提高错误判断的门槛，减少FP
#         elif neg_ratio >= 0.7:  # 从0.5升到0.7
#             confidence = 0.6 + (neg_ratio - 0.7) * 0.8
#             return False, min(0.9, confidence)
    
#     # 修复7：中等证据情况下的平衡判断
#     elif num_tests >= 2:
#         if positive_count >= 2:
#             return True, 0.65
#         elif negative_count >= 2:
#             return False, 0.65
    
#     # 证据不足时返回不确定
#     return 'UNDETERMINED', 0.5

def balanced_fp_fn_fusion(test_results):
    """
    平衡FP和FN的融合方法
    核心策略：对错误检测严格，对正确检测合理
    """
    definite_results = {k: v for k, v in test_results.items() if v != 'UNDETERMINED'}
    if not definite_results:
        return 'UNDETERMINED', 0.5

    num_tests = len(definite_results)
    positive_count = sum(1 for r in definite_results.values() if r is True)
    negative_count = num_tests - positive_count
    pos_ratio = positive_count / num_tests
    
    # =========================
    # 快速错误检测（严格但合理）
    # =========================
    
    # 策略1：高可靠性测试说错误 + 有其他支持
    high_reliability_false = [
        test for test in ['cmt', 'orc'] 
        if test in test_results and test_results[test] is False
    ]
    
    if high_reliability_false:
        # 有高可靠性测试说错误
        supporting_false = negative_count - len(high_reliability_false)
        if supporting_false >= 1 or len(high_reliability_false) >= 2:
            return False, 0.8 + min(0.15, len(high_reliability_false) * 0.1)
    
    # 策略2：多个中等测试说错误
    medium_false_count = sum(
        1 for test in ['sem', 'qrt', 'nrt']
        if test in test_results and test_results[test] is False
    )
    if medium_false_count >= 3:
        return False, 0.75
    
    # =========================
    # 合理正确判断（降低门槛）
    # =========================
    
    # 条件1：基础多数支持
    if pos_ratio >= 0.6:  # 从0.7降回0.6
        # 检查反对证据的强度
        strong_opposition = any(
            test in test_results and test_results[test] is False
            for test in ['cmt', 'orc']  # 只有这些测试的反对才算强反对
        )
        
        if not strong_opposition:
            # 没有强反对证据，可以判断正确
            if pos_ratio >= 0.8:
                confidence = 0.8 + min(0.15, (pos_ratio - 0.8) * 3)
                return True, confidence
            elif pos_ratio >= 0.7:
                return True, 0.75
            else:  # 0.6-0.7区间
                # 需要至少一个高可靠性测试支持
                high_reliability_support = any(
                    test in test_results and test_results[test] is True
                    for test in ['cmt', 'orc']
                )
                if high_reliability_support:
                    return True, 0.7
                else:
                    return True, 0.65  # 较低置信度
    
    # =========================
    # 中等证据处理
    # =========================
    
    # 当证据不够强时，使用加权投票
    test_weights = {
        'cmt': 1.5, 'orc': 1.3, 'sem': 1.1, 'qrt': 1.1,
        'nrt': 1.0, 'stt': 0.9, 'srt': 0.9, 'mst': 0.8
    }
    
    weighted_score = 0
    total_weight = 0
    for test_name, result in definite_results.items():
        weight = test_weights.get(test_name, 1.0)
        if result is True:
            weighted_score += weight
        total_weight += weight
    
    if total_weight > 0:
        weighted_ratio = weighted_score / total_weight
        
        # 使用加权分数做最终决策
        if weighted_ratio >= 0.65 and not high_reliability_false:
            return True, 0.7
        elif weighted_ratio <= 0.35:
            return False, 0.7
    
    # =========================
    # 边缘情况：测试数量较少时的处理
    # =========================
    
    if num_tests >= 3:
        if positive_count >= 2 and negative_count == 0:
            return True, 0.65
        elif negative_count >= 2 and positive_count == 0:
            return False, 0.65
    
    # 默认不确定
    return 'UNDETERMINED', 0.5

def conservative_fusion(test_results):
    """
    极端保守版本：宁可错过正确SQL，也不错判错误SQL
    """
    definite_results = {k: v for k, v in test_results.items() if v != 'UNDETERMINED'}
    if not definite_results:
        return 'UNDETERMINED', 0.5

    num_tests = len(definite_results)
    positive_count = sum(1 for r in definite_results.values() if r is True)
    negative_count = num_tests - positive_count
    
    # 极端保守策略：只有极强证据才判断为正确
    if positive_count == num_tests:  # 所有测试都通过
        return True, 0.9
    elif positive_count >= num_tests - 1 and num_tests >= 5:  # 最多1个测试失败
        # 检查失败的测试是否重要
        failed_tests = [t for t, r in definite_results.items() if r is False]
        unimportant_tests = ['mst', 'srt', 'stt']  # 相对不重要的测试
        
        if all(test in unimportant_tests for test in failed_tests):
            return True, 0.8
    
    # 有任何测试失败就倾向于错误
    if negative_count >= 1:
        # 检查失败测试的重要性
        failed_tests = [t for t, r in definite_results.items() if r is False]
        important_failed = any(test in ['cmt', 'orc', 'sem'] for test in failed_tests)
        
        if important_failed or negative_count >= 2:
            return False, 0.7 + min(0.2, negative_count * 0.1)
    
    # 默认不确定
    return 'UNDETERMINED', 0.5
def reliability_weighted_vote(test_results):
    """
    基于测试方法可靠性的加权投票
    利用已知的测试方法性能差异
    """
    # 基于准确率的权重（从您的评估结果得出）
    reliability_weights = {
        'cmt': 2.5,  # 准确率87.8%
        'orc': 1.5,  # 准确率57.6%，但PP高
        'sem': 1.0,  # 准确率58.9%
        'qrt': 1.0,  # 准确率59.2%
        'nrt': 0.9,  # 准确率55.6%
        'stt': 0.8,  # 准确率51.0%
        'srt': 0.8,  # 准确率50.9%
        'mst': 0.7   # 准确率50.5%
    }
    
    # 计算加权投票
    positive_weight = 0
    negative_weight = 0
    total_weight = 0
    
    for test_name, result in test_results.items():
        if result != 'UNDETERMINED':
            weight = reliability_weights.get(test_name, 0.5)
            if result == True:
                positive_weight += weight
            else:
                negative_weight += weight
            total_weight += weight
    
    if total_weight == 0:
        return 'UNDETERMINED', 0.5
    
    # 计算加权得分
    weighted_score = positive_weight / total_weight
    
    # 动态阈值：考虑有效权重
    effective_ratio = total_weight / sum(reliability_weights.values())
    
    if effective_ratio >= 0.7:  # 高质量测试参与度高
        threshold_correct = 0.65  # 判断正确的阈值较高
        threshold_incorrect = 0.35  # 判断错误的阈值较低
    else:
        threshold_correct = 0.7
        threshold_incorrect = 0.3
    
    # 决策
    if weighted_score >= threshold_correct:
        # 特别检查cmt是否反对（防止FP）
        if 'cmt' in test_results and test_results['cmt'] == False:
            return 'UNDETERMINED', 0.6  # cmt反对时转为不确定
        confidence = 0.6 + (weighted_score - threshold_correct) * 2
        return True, min(0.95, confidence)
    elif weighted_score <= threshold_incorrect:
        confidence = 0.6 + (threshold_incorrect - weighted_score) * 2
        return False, min(0.95, confidence)
    else:
        # cmt有决定性作用
        if 'cmt' in test_results and test_results['cmt'] != 'UNDETERMINED':
            cmt_result = test_results['cmt']
            return cmt_result, 0.7
        return 'UNDETERMINED', 0.5

def reliability_weighted_vote_ablation(test_results):
    """
    支持消融实验的基于可靠性加权投票机制
    - 自动适配任意子集测试组合
    - 保留 cmt 的强反对约束
    - 动态调整阈值和置信度范围
    """

    # ✅ 基于评估结果的可靠性权重
    reliability_weights = {
        'cmt': 1.5,  # 高准确率
        'orc': 0.7,  # 高PP
        'sem': 1.0,
        'qrt': 1.0,
        'nrt': 1.0,
        'sst': 1.0,
        'srt': 1.0,
        'mst': 0.7
    }

    # ✅ 仅保留参与测试的子集
    active_weights = {k: v for k, v in reliability_weights.items() if k in test_results}

    if not active_weights:
        return 'UNDETERMINED', 0.5

    # ===========================
    # Step 1: 加权统计
    # ===========================
    pos_weight = 0.0
    neg_weight = 0.0
    total_weight = 0.0

    for test, result in test_results.items():
        if result == 'UNDETERMINED':
            continue
        weight = active_weights.get(test, 0.5)
        total_weight += weight
        if result:
            pos_weight += weight
        else:
            neg_weight += weight

    if total_weight == 0:
        return 'UNDETERMINED', 0.5

    weighted_score = pos_weight / total_weight

    # ===========================
    # Step 2: 动态阈值调整
    # ===========================
    # 根据当前参与的测试比例，调整判断阈值（越少越保守）
    active_ratio = len(test_results) / len(reliability_weights)

    # 较少测试时，放宽不确定区间，防止过拟合判断
    if active_ratio >= 0.7:
        th_correct = 0.65
        th_incorrect = 0.35
    elif active_ratio >= 0.4:
        th_correct = 0.68
        th_incorrect = 0.32
    else:
        th_correct = 0.7
        th_incorrect = 0.3

    # ===========================
    # Step 3: 决策逻辑
    # ===========================
    # ✅ 若分数高，倾向正确
    if weighted_score >= th_correct:
        # 防止 FP：若 cmt 强烈反对，则降级为不确定
        # if 'cmt' in test_results and test_results['cmt'] is False:
        #     return 'UNDETERMINED', 0.6
        confidence = 0.65 + (weighted_score - th_correct) * 1.5
        return True, min(0.95, confidence)

    # ✅ 若分数低，倾向错误
    elif weighted_score <= th_incorrect:
        confidence = 0.65 + (th_incorrect - weighted_score) * 1.5
        return False, min(0.95, confidence)

    # ✅ 中间区间：依赖 cmt 或保守判不确定
    else:
        if 'cmt' in test_results and test_results['cmt'] != 'UNDETERMINED':
            return test_results['cmt'], 0.7
        return 'UNDETERMINED', 0.5


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
    output_file_dir = os.path.join(os.path.dirname(args.data_file_path), "results", args.nl2sql_model_name) \
        if args.benchmark_name == "spider" else os.path.join(os.path.dirname(args.data_file_path), "results")
    
    # load to-test data and predicted sqls (if applicable)
    data = json.load(open(args.data_file_path))
    if args.benchmark_name in ["spider", "bird"]:
        assert args.predicted_sql_path is not None
        preds = [line.strip() for line in open(args.predicted_sql_path).readlines()]
        assert len(preds) == len(data)

    base_output_file_name = (f"judgments,dataset={args.benchmark_name}"
                   f"{f'+{args.nl2sql_model_name}' if hasattr(args, 'nl2sql_model_name') and args.nl2sql_model_name else ''},judge=")
    mst_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(sem)(minimum-syntax).jsonl")
    mst_judgments = [json.loads(line) for line in open(mst_output_file_path)] if os.path.exists(mst_output_file_path) else []
    sem_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(sem)(semantic-check).jsonl")
    sem_judgments = [json.loads(line) for line in open(sem_output_file_path)] if os.path.exists(mst_output_file_path) else []
    orc_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(orc).jsonl")
    orc_judgments = [json.loads(line) for line in open(orc_output_file_path)] if os.path.exists(orc_output_file_path) else []
    srt_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(mtp)(relax).jsonl")
    srt_judgments = [json.loads(line) for line in open(srt_output_file_path)] if os.path.exists(srt_output_file_path) else []
    sst_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(mtp)(strengthen).jsonl")
    sst_judgments = [json.loads(line) for line in open(sst_output_file_path)] if os.path.exists(sst_output_file_path) else []
    cmt_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(dif)(cross-model).jsonl")
    cmt_judgments = [json.loads(line) for line in open(cmt_output_file_path)] if os.path.exists(cmt_output_file_path) else []
    sct_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(dif)(self-consistency).jsonl")
    sct_judgments = [json.loads(line) for line in open(sct_output_file_path)] if os.path.exists(sct_output_file_path) else []
    nrt_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(exp)(nl-query).jsonl")
    nrt_judgments = [json.loads(line) for line in open(nrt_output_file_path)] if os.path.exists(nrt_output_file_path) else []
    qrt_output_file_path = os.path.join(output_file_dir, base_output_file_name + "guardian(exp)(sql-query).jsonl")
    qrt_judgments = [json.loads(line) for line in open(qrt_output_file_path)] if os.path.exists(qrt_output_file_path) else []
    check_model_outputs_consistency(args.esemble_mode, sem_output_file_path, orc_output_file_path, srt_output_file_path, cmt_output_file_path, nrt_output_file_path)
    
    esemble_output_file_path = os.path.join(output_file_dir, base_output_file_name + f"guardian({args.esemble_mode}).jsonl")
    # judgments = open(esemble_output_file_path, 'w')
    judgments = []
    # 初始化融合器
    for i in range(len(data)):
        # judgment, _, = reliability_weighted_vote_ablation(
        judgment, _, = enhanced_strict_majority_vote_ablation(
            {
                **({"mst": mst_judgments[i]["judgment"]} if "sem" in args.esemble_mode else {}),
                **({"sem": sem_judgments[i]["judgment"]} if "sem" in args.esemble_mode else {}),
                **({"orc": orc_judgments[i]["judgment"]} if "orc" in args.esemble_mode else {}),
                **({"srt": srt_judgments[i]["judgment"]} if "mtp" in args.esemble_mode else {}),
                **({"sst": sst_judgments[i]["judgment"]} if "mtp" in args.esemble_mode else {}),
                **({"cmt": cmt_judgments[i]["judgment"]} if "dif" in args.esemble_mode else {}),
                **({"sct": cmt_judgments[i]["judgment"]} if args.benchmark_name == "spider" and "dif" in args.esemble_mode else {}),
                **({"nrt": nrt_judgments[i]["judgment"]} if "exp" in args.esemble_mode else {}),
                **({"qrt": qrt_judgments[i]["judgment"]} if "exp" in args.esemble_mode else {}),
            }
        )
        judgments.append({
            **({"SEM Test": mst_judgments[i]["judgment"] and sem_judgments[i]["judgment"]} if "sem" in args.esemble_mode else {}),
            **({"ORC Test": orc_judgments[i]["judgment"]} if "orc" in args.esemble_mode else {}),
            **({"MTP Test": srt_judgments[i]["judgment"] and sst_judgments[i]["judgment"]} if "mtp" in args.esemble_mode else {}),
            **({"DIF Test": cmt_judgments[i]["judgment"]} if "dif" in args.esemble_mode else {}),
            **({"EXP Test": nrt_judgments[i]["judgment"] and qrt_judgments[i]["judgment"]} if "exp" in args.esemble_mode else {}),
            "judgment": judgment
        })
    
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