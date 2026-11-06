export function hasConfirmedExecution(data={}) {
  // Historical success remains complete even when it is outside today's list.
  if(data.onboarding?.has_successful_run===true)return true;
  return (data.runs||[]).some(run=>run?.status==='success');
}
