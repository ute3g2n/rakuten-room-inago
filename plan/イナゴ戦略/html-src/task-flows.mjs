import { mkdir, writeFile } from 'node:fs/promises';
import { renderReadableFlow } from './flow-renderer.mjs';
import { flowNames, flowOrder, flowRoles } from './flow-layout.mjs';

export const flows = [
  {
    "id": "F00",
    "title": "前日の投稿結果を回収・反映し、F02の手順2へ",
    "desc": "各手順の担当者は、何のために、何をどうするかを全文で説明します。条件や注意点も文章で示します。"
  },
  {
    "id": "F02",
    "title": "T02 日次：前工程が終わったら、すぐ成果取得",
    "desc": "各手順の担当者は、何のために、何をどうするかを全文で説明します。条件や注意点も文章で示します。"
  },
  {
    "id": "F03",
    "title": "T02の検索ループ：人気語から15本文へ",
    "desc": "各手順の担当者は、何のために、何をどうするかを全文で説明します。条件や注意点も文章で示します。"
  },
  {
    "id": "F04",
    "title": "本文生成と保存：誰が何を受け渡すか",
    "desc": "各手順の担当者は、何のために、何をどうするかを全文で説明します。条件や注意点も文章で示します。"
  },
  {
    "id": "F05",
    "title": "T03 独立した本人投稿・見送り・訂正（例：21:30）",
    "desc": "各手順の担当者は、何のために、何をどうするかを全文で説明します。条件や注意点も文章で示します。"
  },
  {
    "id": "F06",
    "title": "T04 毎日の成果取得：取得と解釈を分ける",
    "desc": "各手順の担当者は、何のために、何をどうするかを全文で説明します。条件や注意点も文章で示します。"
  },
  {
    "id": "F07",
    "title": "T05・T06 成果閲覧と本人による設定変更",
    "desc": "各手順の担当者は、何のために、何をどうするかを全文で説明します。条件や注意点も文章で示します。"
  },
  {
    "id": "F08",
    "title": "T07・T08 状態確認と復旧",
    "desc": "各手順の担当者は、何のために、何をどうするかを全文で説明します。条件や注意点も文章で示します。"
  },
  {
    "id": "F09",
    "title": "T09 日次バックアップと月次復元確認",
    "desc": "各手順の担当者は、何のために、何をどうするかを全文で説明します。条件や注意点も文章で示します。"
  },
  {
    "id": "F10",
    "title": "T10 旧履歴移行：証拠のある実投稿だけ",
    "desc": "各手順の担当者は、何のために、何をどうするかを全文で説明します。条件や注意点も文章で示します。"
  },
  {
    "id": "F11",
    "title": "T11 実装テスト → 本番 → 随時デバッグ",
    "desc": "各手順の担当者は、何のために、何をどうするかを全文で説明します。条件や注意点も文章で示します。"
  }
];
flows.forEach(flow => { flow.title = flowNames[flow.id]; flow.desc = flowRoles[flow.id] + '各担当が何のために、何をどうするかを全文で説明します。'; });
flows.sort((a,b) => flowOrder.indexOf(a.id) - flowOrder.indexOf(b.id));
export const renderFlow = renderReadableFlow;
export async function writeFlowAssets() {
  const dir = new URL('../図解/', import.meta.url);
  await mkdir(dir, { recursive: true });
  for (const flow of flows) await writeFile(new URL(flow.id + '.svg', dir), renderReadableFlow(flow), 'utf8');
}
