// One canonical implementation for account, journal and overview workflows.
export function installPages({state}) {
  const view=new URLSearchParams(location.search).get('view');
  const canonical={accounts:'/accounts',runs:'/runs',overview:'/dashboard'};
  if(canonical[view]){location.replace(canonical[view]);return;}
  if(view!=='updates')return;
  // Version management has one canonical React surface. Keep this legacy
  // deep-link as a compatibility redirect so it cannot grow a second updater.
  if(state.user.role==='admin') location.replace('/admin/settings');
  else location.replace('/dashboard');
}
