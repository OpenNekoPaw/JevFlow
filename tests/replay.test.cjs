const test=require('node:test'),assert=require('node:assert/strict');
const {initial,apply,frame,orderedRuns,count}=require('../jevflow/web/replay.js');
test('seeking hides future answers and terminal report and is reversible',()=>{
 const data={report:{result:'future'},flowHash:'v1',replay:{version:1,initial:initial(),events:[
 {offsetMs:1,patches:[{path:['trace','steps',0],value:{node:'a',status:'running'}}]},
 {offsetMs:2,patches:[{path:['trace','steps',0,'status'],value:'completed'},{path:['trace','steps',0,'answers'],value:{ok:true}}]},
 {offsetMs:3,patches:[{path:['report'],value:{result:'done'}},{path:['finished'],value:true}]}]}};
 assert.equal(frame(data,0).trace.steps.length,0);assert.equal(frame(data,0).report,undefined);
 assert.equal(frame(data,1).trace.steps[0].answers,undefined);
 assert.deepEqual(frame(data,2).trace.steps[0].answers,{ok:true});assert.equal(frame(data,2).report,undefined);
 assert.equal(frame(data,3).report.result,'done');assert.equal(frame(data,1).trace.steps[0].answers,undefined);
 assert.equal(data.replay.initial.trace.steps.length,0);
});
test('legacy trace playback preserves separate loop visits and hides future steps',()=>{
 const data={status:'completed',finished:true,trace:{steps:[{node:'a',status:'completed',answer:1},{node:'a',status:'completed',answer:2}]},report:{result:2}};
 assert.equal(count(data),2);assert.equal(frame(data,1).trace.steps.length,1);assert.equal(frame(data,1).report,undefined);
 assert.equal(frame(data,2).trace.steps[1].answer,2);
});
test('session ordering uses run timestamps and actor filters without merging versions',()=>{
 const runs=[{runId:'b',receivedAtMs:20,finished:true,flowHash:'v2',context:{sessionId:'s',actorId:'x'}},{runId:'a',receivedAtMs:10,finished:true,flowHash:'v1',context:{sessionId:'s',actorId:'x'}},{runId:'c',receivedAtMs:5,finished:true,context:{sessionId:'other'}},{runId:'d',receivedAtMs:15,finished:false,context:{sessionId:'s',actorId:'x'}}];
 assert.deepEqual(orderedRuns(runs,'s','x').map(r=>[r.runId,r.flowHash]),[['a','v1'],['b','v2']]);
});
test('JSON patch dictionary keys cannot mutate object prototypes',()=>{
 const state=initial();apply(state,[{path:['trace','__proto__'],value:{polluted:true}}]);assert.equal({}.polluted,undefined);assert.equal(Object.prototype.hasOwnProperty.call(state.trace,'__proto__'),true);
 assert.throws(()=>apply(initial(),[{path:['trace','__proto__','polluted'],value:true}]),/Invalid replay path/);
});
test('incremental playback cache supports rewind and newly appended events',()=>{
 const data={replay:{version:1,initial:initial(),events:[{offsetMs:1,patches:[{path:['trace','steps',0],value:{node:'a',status:'running'}}] }]}};
 const cache={},first=frame(data,1,cache);data.replay.events.push({offsetMs:2,patches:[{path:['trace','steps',0,'status'],value:'completed'}]});
 assert.equal(frame(data,2,cache).trace.steps[0].status,'completed');assert.equal(first.trace.steps[0].status,'running');assert.equal(frame(data,0,cache).trace.steps.length,0);assert.equal(frame(data,1,cache).trace.steps[0].status,'running');
});
