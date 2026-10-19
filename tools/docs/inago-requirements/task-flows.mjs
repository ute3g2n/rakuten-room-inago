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
  },
  {
    "id": "F12",
    "title": "日次後の保守確認から障害対応・次回観測まで",
    "desc": "同じ案件で内容ある成果を次担当へ渡し、失敗を記録しながら調査から報告の公開と終了登録まで進みます。"
  }
];
flows.forEach(flow => { flow.title = flowNames[flow.id]; flow.desc = flowRoles[flow.id] + '各担当が何のために、何をどうするかを全文で説明します。'; });
flows.find(flow => flow.id === 'F12').desc += '工程の完遂、操作の成否、公開、元障害の解消を別々に記録し、原因不明でも成果を渡して次担当を実起動します。各操作は開始・終了・失敗を相関IDと実行版で記録し、通常ログや管理DBへ書けない場合は独立した追記ログへ退避して、次回起動時に照合します。調査と計画の失敗時は、確認範囲・不明・次の行動を含むcontroller作成の成果を担当AIの成功と区別して保存します。修正担当は既存変更を保ち、固定されたプロジェクトroot内だけで作業します。各stageの引継ぎファイル、repair/report attemptと出力hash、最終HTML、公開先の現在内容を照合してから工程完遂を登録します。試験未実施・review unavailable・反映未実施もそのまま記録し、成功や障害解消には読み替えません。reportのtarget/identity不一致、remote push、publication sidecar保存の失敗は具体的reason・例外連鎖・捕捉位置・相関ID・入力/成果物参照を残し、unknown/failedで終端照合を続けます。';
flows.sort((a,b) => flowOrder.indexOf(a.id) - flowOrder.indexOf(b.id));
export const renderFlow = renderReadableFlow;
export async function writeFlowAssets() {
  const dir = new URL('../../../docs/specifications/inago/図解/', import.meta.url);
  await mkdir(dir, { recursive: true });
  for (const flow of flows) await writeFile(new URL(flow.id + '.svg', dir), renderReadableFlow(flow), 'utf8');
}
