import test from 'node:test';
import assert from 'node:assert/strict';
import {framePoint} from './cloud-frame-coordinates.js';

test('a letterboxed screenshot maps only painted pixels into remote coordinates',()=>{
  const rect={left:20,top:30,width:360,height:300};
  assert.equal(framePoint(rect,1280,720,200,45),null);
  assert.deepEqual(framePoint(rect,1280,720,200,180),{x:.5,y:.5});
  assert.deepEqual(framePoint(rect,1280,720,20,78.75),{x:0,y:0});
});
test('zero sized frames cannot produce a remote click',()=>{
  assert.equal(framePoint({left:0,top:0,width:0,height:0},1280,720,0,0),null);
});
