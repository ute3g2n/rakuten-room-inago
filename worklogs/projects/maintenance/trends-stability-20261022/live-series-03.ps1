@'
import json,time,hashlib,os
from pathlib import Path
from datetime import datetime,timezone,timedelta
from automation.inago.production_runtime import load_production_config
from automation.inago.live_sources import PublicBrowserSource,PublicSourceConfig
from automation.inago.trends_adapter import TrendsRequest,parse_grouped_top_response
root=Path.cwd();runtime=root/'rakuten-room-inago'/'runtime';out=runtime/'diagnostics'/'trends-stability-20261022';cfg=load_production_config(runtime/'config'/'settings.json',data_root=runtime)
words=['スキンケア','収納','コスメ','調味料','靴下']

records=[];before=hashlib.sha256((runtime/'state.sqlite').read_bytes()).hexdigest()
for i,word in enumerate(words,1):
 source=PublicBrowserSource(PublicSourceConfig(chrome_executable=cfg.chrome_executable,evidence_root=out/'live-series-03'/'observations',environment='public_ui',timeout_seconds=120,node_executable=cfg.node_executable,headless=False,user_data_dir=cfg.user_data_dir))
 req=TrendsRequest(request_id=f'DEBUG-TRENDS-20261022-SERIES-03-{i:02}',run_id='DEBUG-TRENDS-20261022-SERIES-03',settings_version=str(cfg.adopted_settings['version']),parent_word=word,range='3m',deadline_at=(datetime.now(timezone.utc)+timedelta(seconds=120)).isoformat())
 print(json.dumps({'event':'started','index':i,'word':word,'pid':os.getpid()},ensure_ascii=False),flush=True)
 start=time.monotonic();response=source.fetch(req);parsed=parse_grouped_top_response(response,req)
 record={'index':i,'word':word,'at':datetime.now(timezone.utc).isoformat(),'duration_seconds':round(time.monotonic()-start,2),'status':parsed.status,'error_code':parsed.error_code,'word_count':len(parsed.words),'ranks':[w.source_rank for w in parsed.words],'period':parsed.ui_period_label}
 records.append(record);after=hashlib.sha256((runtime/'state.sqlite').read_bytes()).hexdigest()
 (out/'live-series-03-results.json').write_text(json.dumps({'source_sha256':hashlib.sha256((root/'automation/inago/adapters/public_sources.mjs').read_bytes()).hexdigest(),'database_before_sha256':before,'database_after_sha256':after,'database_unchanged':before==after,'records':records},ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(record,ensure_ascii=False),flush=True)
 if parsed.status!='observed': break
 if i<len(words): time.sleep(180)
'@ | & .venv-inago/Scripts/python.exe -X utf8 -B -
