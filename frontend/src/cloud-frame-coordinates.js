export function framePoint(rect,width,height,clientX,clientY) {
  if(!(rect.width>0&&rect.height>0&&width>0&&height>0))return null;
  const scale=Math.min(rect.width/width,rect.height/height);
  const paintedWidth=width*scale,paintedHeight=height*scale;
  const left=rect.left+(rect.width-paintedWidth)/2,top=rect.top+(rect.height-paintedHeight)/2;
  const x=(clientX-left)/paintedWidth,y=(clientY-top)/paintedHeight;
  return x<0||x>1||y<0||y>1?null:{x,y};
}
