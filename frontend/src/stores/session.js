export const state = {session: JSON.parse(sessionStorage.getItem('demo-session') || 'null'), spec:null,
                      cases:[], runs:[], evidence:[], audit:[], draft:null,
                      auditoriums:[], programmeDetail:null, programmeDraft:null,
                      preview:null, notice:''};

export function setSession(session) {
  state.session = session;
  state.spec = null;
  state.draft = null;
  state.auditoriums = [];
  state.programmeDetail = null;
  state.programmeDraft = null;
  state.preview = null;
  state.notice = '';
  if (session) sessionStorage.setItem('demo-session', JSON.stringify(session));
  else sessionStorage.removeItem('demo-session');
}
