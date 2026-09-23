"use strict";
// One pending candidate, plus at most three seconds of compressed source frames.
class VideoCandidates {
  constructor(){this.frames=[];this.pending=null;this.expired=0;this.merged=0;this.lastTrigger=-Infinity;}
  prune(t){
    this.frames=this.frames.filter(f=>t-f.t<=3).slice(-180);
    if(this.pending!==null&&t-this.pending>1.5){this.expired++;this.pending=null;}
  }
  add(frame,motion){
    this.prune(frame.t);
    if(this.frames.length&&frame.t<=this.frames.at(-1).t)return;
    this.frames.push(frame);this.frames=this.frames.slice(-180);
    if(motion>=10&&frame.t-this.lastTrigger>=.35){
      if(this.pending!==null)this.merged++;
      this.pending=frame.t;this.lastTrigger=frame.t;
    }
  }
  take(t){this.prune(t);const trigger=this.pending;this.pending=null;return trigger;}
  select(t,limit=8){
    const frames=this.frames.filter(f=>f.t<=t&&t-f.t<=1.5);
    if(frames.length<=limit)return frames;
    return Array.from({length:limit},(_,i)=>frames[Math.round(i*(frames.length-1)/(limit-1))]);
  }
}
if(typeof module!=="undefined")module.exports=VideoCandidates;
else window.VideoCandidates=VideoCandidates;

// Count presented video frames, never infer frame rate from a timer.
class FrameStride {
  constructor(){this.origin=null;this.bucket=-1;this.missed=0;}
  accept(index){
    if(!Number.isFinite(index))return false;
    if(this.origin===null)this.origin=index;
    const bucket=Math.floor((index-this.origin)/10);
    if(bucket<=this.bucket)return false;
    this.missed+=Math.max(0,bucket-this.bucket-1);
    this.bucket=bucket;return true;
  }
}
if(typeof module!=="undefined")module.exports.FrameStride=FrameStride;
else window.FrameStride=FrameStride;

// Keep a bounded current clip while inference runs, including the last submitted
// frame as onset context. Select across the interval, never only its tail.
class ReplayWindow {
  constructor(){this.frames=[];}
  prune(now){this.frames=this.frames.filter(frame=>now-frame.t<=4).slice(-32);}
  add(frame){
    if(this.frames.length&&frame.t<=this.frames.at(-1).t)return;
    this.frames.push(frame);this.prune(frame.t);
  }
  select(now,after,limit=6){
    this.prune(now);
    const before=this.frames.filter(frame=>frame.t<=after).at(-1);
    const frames=this.frames.filter(frame=>frame.t>after);
    if(before)frames.unshift(before);
    if(frames.length<=limit)return frames;
    return Array.from({length:limit},(_,i)=>frames[Math.round(i*(frames.length-1)/(limit-1))]);
  }
}
if(typeof module!=="undefined")module.exports.ReplayWindow=ReplayWindow;
else window.ReplayWindow=ReplayWindow;

// A color cue only queues a panel for OCR; it never assigns heroes or teams.
class PanelFrameBuffer {
  constructor(){this.frame=null;}
  static score(pixels,width=64){
    let blue=0,total=0;
    for(let y=6;y<29;y++)for(let x=12;x<53;x++){
      const i=(y*width+x)*4,r=pixels[i],g=pixels[i+1],b=pixels[i+2];
      if(b>35&&b>r*1.3&&g>r*1.1)blue++;total++;
    }
    return blue/total;
  }
  add(frame,score){if(score>=.6)this.frame=frame;}
  take(now,after){const frame=this.frame;this.frame=null;return frame&&frame.t>after&&now-frame.t<=15?frame:null;}
}
if(typeof module!=="undefined")module.exports.PanelFrameBuffer=PanelFrameBuffer;
else window.PanelFrameBuffer=PanelFrameBuffer;
