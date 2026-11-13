const assert=require('node:assert/strict');const test=require('node:test');const ui=require('../app/static/user/app.js');
const points=[[78.14,11.66],[78.141,11.66],[78.141,11.661],[78.14,11.661]];
test('boundary is closed for GeoJSON export and area is in square metres',()=>{assert.equal(ui.validate(points),'');assert(ui.area(points)>10000);const ring=ui.feature(points).geometry.coordinates[0];assert.deepEqual(ring[0],ring.at(-1));assert.equal(points.length,4);});
test('crossed and incomplete boundaries cannot be saved',()=>{assert.match(ui.validate([[78,11],[79,12],[78,12],[79,11]]),/crosses/);assert.match(ui.validate(points.slice(0,2)),/three/);assert.match(ui.validate([...points,points[0]]),/same position/);});
