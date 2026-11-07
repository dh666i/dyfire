import contracts from './api-contracts.json' with {type:'json'};

// A small validator for the Pydantic-exported subset. Unknown fields stay
// forward compatible; required data and state enums never silently disappear.
function valid(value,schema,root) {
  if(schema.$ref)return valid(value,root.$defs[schema.$ref.split('/').at(-1)],root);
  if(schema.anyOf)return schema.anyOf.some(branch=>valid(value,branch,root));
  if(schema.enum&&!schema.enum.includes(value))return false;
  if(schema.type==='null')return value===null;
  if(schema.type==='string')return typeof value==='string';
  if(schema.type==='boolean')return typeof value==='boolean';
  if(schema.type==='integer')return Number.isInteger(value);
  if(schema.type==='number')return typeof value==='number'&&Number.isFinite(value);
  if(schema.type==='array')return Array.isArray(value)&&value.every(item=>valid(item,schema.items||{},root));
  if(schema.type==='object') {
    if(value===null||typeof value!=='object'||Array.isArray(value))return false;
    if((schema.required||[]).some(key=>!(key in value)))return false;
    return Object.entries(schema.properties||{}).every(([key,field])=>!(key in value)||valid(value[key],field,root));
  }
  return true;
}

export function assertApiContract(path,value) {
  const pathname=path.split('?')[0];
  const name=pathname==='/api/app/bootstrap'?'app':/^\/api\/task-probes\/[^/]+$/.test(pathname)?'probe':null;
  if(name&&!valid(value,contracts[name],contracts[name])) {
    const error=new Error('服务返回的数据格式不完整，请刷新页面；持续出现时请提供排障编号。');
    error.code='API_CONTRACT_MISMATCH';throw error;
  }
  return value;
}
