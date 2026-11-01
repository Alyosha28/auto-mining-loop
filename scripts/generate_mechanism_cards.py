#!/usr/bin/env python3
"""Mechanism-only local scout; no formula grammar, prior library or outcomes."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research.local_idea_generation import request_model,write_json
from research.local_model_runtime import resolve_loaded_model,verify_response_model

KEYS=['id','name','mechanism','observable_pattern','measurement_steps','expected_relation','failure_condition']
SCHEMA={'type':'object','additionalProperties':False,'properties':{'ideas':{'type':'array','minItems':3,'maxItems':3,'items':{
 'type':'object','additionalProperties':False,'properties':{k:{'type':'string'} for k in KEYS},'required':KEYS}}},'required':['ideas']}

def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
 p.add_argument('--model',help='Exact loaded LM Studio identifier or modelKey; optional only with one loaded LLM')
 p.add_argument('--max-tokens',type=int,default=2600)
 a=p.parse_args(argv)
 if a.max_tokens<=0:p.error('--max-tokens must be positive')
 model=resolve_loaded_model(a.model)
 result=request_model('http://127.0.0.1:1234/v1',model,
  '你是一位独立的量化研究提案者。只提出可证伪的经济机制，不写程序或公式语言，不自评为有效。用中文给出三个完整而不同的机制卡，不能输出省略号、占位符或空字段。不要假设对话历史。',
  {'available_data':'A股逐日开高低收价格、成交股数、成交金额（元）、换手率（比例）。完整市场交易日历；缺失不能当零，停牌不能压缩，跨权息窗口排除。没有盘口、逐笔买卖方向、基本面、新闻或真实订单数据。',
   'prediction_task':'每周最后交易日收盘计算截面信号；下一交易日开盘进入，到下一周信号后的下一交易日开盘退出，约持有一周。不赚信号日收盘到入场开盘的跳空。',
   'request':'提出恰好3个不同机制。每张卡说明机制、应看到的历史现象、可由上述数据实现的测量步骤、预期与未来持有期收益的关系、什么结果推翻解释。不需要完整数学公式，不得把成交量视为带符号订单流；不要只改技术指标窗口。你不接收已有库、其他候选或历史得分。'},a.output,schema=SCHEMA,seed=20260925,max_tokens=a.max_tokens)
 verify_response_model(a.output,model)
 if set(result)!= {'ideas'} or len(result['ideas'])!=3:raise ValueError('three complete cards required')
 for row in result['ideas']:
  if set(row)!=set(KEYS) or any(not isinstance(v,str) or v.strip() in ('','...','…','TODO') for v in row.values()):raise ValueError('incomplete mechanism card')
 write_json(a.output/'validated_cards.json',result)
 print('THREE_MECHANISM_CARDS_RECEIVED',flush=True)


if __name__=='__main__':
 main()
