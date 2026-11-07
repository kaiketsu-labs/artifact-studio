/* Rebuild the checked-in offline editor and retain each bundled dependency's license. */
import {build} from 'esbuild';
import {readFile,writeFile,access,rm} from 'node:fs/promises';
import {dirname,resolve} from 'node:path';

const output='../../web/vendor/codemirror.js';
// esbuild omits this file when there are no linked comments. Remove stale
// comments and always emit the same three managed files for update automation.
await rm(output+'.LEGAL.txt',{force:true});
const result=await build({entryPoints:['editor.js'],bundle:true,minify:true,format:'iife',globalName:'ArtifactTextEditor',target:'es2022',outfile:output,legalComments:'linked',metafile:true});
try{await access(output+'.LEGAL.txt');}catch{await writeFile(output+'.LEGAL.txt','/* Dependency licenses: codemirror-LICENSE.txt */\n');}
const packages=new Set();
for(const input of Object.keys(result.metafile.inputs)){
  if(!input.startsWith('node_modules/'))continue;
  let directory=dirname(resolve(input));
  while(directory!==dirname(directory)){
    try{await access(resolve(directory,'package.json'));packages.add(directory);break;}catch{directory=dirname(directory);}
  }
}
const licenses=[];
for(const directory of [...packages].sort()){
  const metadata=JSON.parse(await readFile(resolve(directory,'package.json'),'utf8'));
  let license;
  for(const name of ['LICENSE','LICENSE.txt','LICENSE.md']){
    try{license=await readFile(resolve(directory,name),'utf8');break;}catch{}
  }
  if(!license)throw Error(`Missing license for ${metadata.name}`);
  licenses.push(`${metadata.name}@${metadata.version}\n${license.trim()}`);
}
await writeFile('../../web/vendor/codemirror-LICENSE.txt',licenses.join('\n\n'+ '='.repeat(72)+'\n\n')+'\n');
