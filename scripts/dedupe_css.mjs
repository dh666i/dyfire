// Remove only an earlier byte-equivalent rule in the same cascade scope.
// Declaration order, media scopes and the final occurrence stay untouched.
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const postcss=require('postcss');
const apply=process.argv.includes('--apply');
const files=['react.css','react-polish.css','react-fire.css'];let removed=0;
for(const file of files){
  const path=new URL('../frontend/src/'+file,import.meta.url),source=fs.readFileSync(path,'utf8');
  const root=postcss.parse(source);let count=0;
  function visit(parent){
    const seen=new Map();
    for(const node of [...(parent.nodes||[])].reverse()){
      if(node.type==='rule'&&node.nodes.every(child=>child.type==='decl'||child.type==='comment')){
        const declarations=node.nodes.filter(child=>child.type==='decl').map(child=>[child.prop,child.value,Boolean(child.important)]);
        const key=JSON.stringify([node.selector,declarations]);
        if(seen.has(key)){node.remove();count++}else seen.set(key,node);
      }else if(node.nodes)visit(node);
    }
  }
  visit(root);removed+=count;
  if(apply&&count)fs.writeFileSync(path,root.toString());
  process.stdout.write(JSON.stringify({file,duplicates:count,applied:apply})+'\n');
}
if(process.argv.includes('--check')&&removed)process.exitCode=1;
