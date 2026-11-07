/* Verify every shipped bundle/license, including unexpected new output files. */
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../../',import.meta.url));
const result=spawnSync('git',['status','--porcelain','--untracked-files=all','--','web/vendor/codemirror.js','web/vendor/codemirror.js.LEGAL.txt','web/vendor/codemirror-LICENSE.txt'],{cwd:root,encoding:'utf8'});
if(result.status!==0)throw Error(result.stderr||'Could not inspect the generated editor files.');
if(result.stdout.trim()){
  console.error('The shipped editor differs from its lockfile. Run npm ci --ignore-scripts && npm run build in scripts/text-editor and commit all generated bundle/license files.');
  process.exitCode=1;
}
