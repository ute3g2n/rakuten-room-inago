import { readFile, writeFile } from 'node:fs/promises';
import {loadDocument,serializeDocument,base,htmlName} from './document-source.mjs';
import {readableChapter28} from './readable-chapter.mjs';
const data=await loadDocument();
data.source=data.source.replace(/^## 28\. [\s\S]*$/m,readableChapter28());
const p=new URL(htmlName,base);
await writeFile(p,(await readFile(p,'utf8')).replace(/(<script id="requirements-source" type="application\/json">)[\s\S]*?(<\/script>)/,(_,a,b)=>a+serializeDocument(data)+b));
