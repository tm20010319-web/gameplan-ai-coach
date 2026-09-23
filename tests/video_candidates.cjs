const {test}=require('node:test');
const assert=require('node:assert/strict');
const Buffer=require('../static/video-candidates.js');
test('high rate capture stays bounded and preserves ordered representative frames',()=>{
 const b=new Buffer();for(let i=0;i<600;i++)b.add({t:i/60,image_base64:String(i)},0);
 assert.ok(b.frames.length<=180);assert.ok(b.frames[0].t>=599/60-3);
 const selected=b.select(599/60,7);assert.equal(selected.length,7);
 assert.equal(selected.at(-1).t,599/60);
 assert.ok(selected.every((f,i)=>!i||f.t>selected[i-1].t));
});
test('motion candidates merge and expire instead of forming an unbounded queue',()=>{
 const b=new Buffer();b.add({t:0},20);b.add({t:.5},20);
 assert.equal(b.pending,.5);assert.equal(b.merged,1);
 b.prune(2.1);assert.equal(b.pending,null);assert.equal(b.expired,1);
 b.add({t:2.2},20);assert.equal(b.take(2.3),2.2);assert.equal(b.pending,null);
});
test('repeated media timestamps cannot masquerade as independent frames',()=>{
 const b=new Buffer();b.add({t:1},0);b.add({t:1},30);assert.equal(b.frames.length,1);assert.equal(b.pending,null);
});

const {FrameStride}=require('../static/video-candidates.js');
test('frame stride selects frames 1 11 21 31 41 51 at 60fps and three frames at 30fps',()=>{
 for(const fps of [60,30]){
  const stride=new FrameStride(),selected=[];
  for(let i=1;i<=fps;i++)if(stride.accept(i))selected.push(i);
  assert.deepEqual(selected,Array.from({length:fps/10},(_,i)=>1+i*10));
 }
});
test('repeated callbacks do not capture duplicates and callback gaps are counted',()=>{
 const stride=new FrameStride();assert.equal(stride.accept(1),true);assert.equal(stride.accept(1),false);
 assert.equal(stride.accept(31),true);assert.equal(stride.missed,2);
});

const {PanelFrameBuffer}=require('../static/video-candidates.js');
test('brief panel survives ordinary frames but expires and never rewinds results',()=>{
 const b=new PanelFrameBuffer();b.add({t:2},.8);b.add({t:5},.1);
 assert.equal(b.take(8,1).t,2);assert.equal(b.take(8,1),null);
 b.add({t:2},.8);assert.equal(b.take(18,1),null);
 b.add({t:2},.8);assert.equal(b.take(8,3),null);
});
