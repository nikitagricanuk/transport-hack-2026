/** Incremental feature-state operations: a slider must not clear every road. */
export function loadColorChanges(previous:Map<string|number,number>,next:Map<string|number,number>){
  const remove=[...previous.keys()].filter(id=>!next.has(id));
  const set=[...next].filter(([id,vc])=>previous.get(id)!==vc).map(([id,vc])=>({id,vc}));
  return {remove,set};
}
