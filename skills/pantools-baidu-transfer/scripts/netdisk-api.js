const fs=require('fs');
const { connect } = require('./lib');
(async () => {
  const { context } = await connect();
  const disk = context.pages().find(p=>p.url().includes('pan.baidu.com'));
  if(!disk){console.log('no baidu tab');process.exit(1);}
  const out = await disk.evaluate(async () => {
    const dir='/AI视频保存';
    let all=[], page=1, errno=null;
    for(;page<=20;page++){
      const url=`/api/list?order=name&desc=0&dir=${encodeURIComponent(dir)}&num=1000&page=${page}&web=1&clienttype=0`;
      const r=await fetch(url,{credentials:'include'});
      const j=await r.json();
      errno=j.errno;
      const list=j.list||[];
      all=all.concat(list.map(x=>x.server_filename));
      if(list.length<1000) break;
    }
    return {errno, count:all.length, names:all};
  });
  console.log('api errno:', out.errno, '| count:', out.count);
  fs.writeFileSync('/Users/aaron/workspace/aiDownload/AI保存视频的中间件/playwright/netdisk-names.json', JSON.stringify(out.names,null,2));
  process.exit(0);
})().catch(e=>{console.error('ERR',e.message);process.exit(1);});
