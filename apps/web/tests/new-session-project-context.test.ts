import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { inheritedDraftProjectContext } from '../src/hooks/code/draftProjectContext'

const workspace = {
  workspace_id: 'zy',
  title: 'zy',
  path: '/workspace/zy',
  status: 'ok',
  session_ids: [],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
  git_branch: 'main',
} as const

assert.equal(inheritedDraftProjectContext({
  workspace: null,
  worktree: false,
  sourceRef: '',
}), null, 'an ordinary conversation must create another ordinary conversation')

assert.deepEqual(inheritedDraftProjectContext({
  workspace,
  worktree: true,
  sourceRef: 'refs/heads/release',
}), {
  workspace,
  worktree: true,
  sourceRef: 'refs/heads/release',
}, 'a project conversation must inherit only its draft project configuration')

assert.equal(inheritedDraftProjectContext({
  workspace,
  worktree: false,
  sourceRef: '',
})?.sourceRef, 'refs/heads/main', 'the current project branch is the safe fallback')

const appShell = readFileSync('src/App.vue', 'utf8')
const runtime = readFileSync('src/hooks/code/useHostCodeRuntime.ts', 'utf8')
const picker = readFileSync('src/ui/code/WorkspaceContextPicker.vue', 'utf8')
const choiceList = readFileSync('src/ui/code/WorkspaceChoiceList.vue', 'utf8')

assert.doesNotMatch(runtime, /recommendRecent|function recommend\(/, 'recent projects must never be selected implicitly')
assert.doesNotMatch(runtime, /selectDshWorkspace|async function choose\(/, 'conversation project selection must use saved bindings instead of reopening the folder picker')
assert.match(appShell, /icon === 'plus'\) startLocalSession\(\{ inheritCurrentProject: true \}\)/)
assert.match(appShell, /@click="\(\) => startLocalSession\(\{ inheritCurrentProject: true \}\)"/)
assert.match(appShell, /startLocalSession\(\{ shouldNavigate: currentView\.value === 'chat' \}\)/)
assert.match(picker, /'切换项目…'/)
assert.match(picker, /'设为普通对话'/)
assert.doesNotMatch(picker, /'移除项目'/)
assert.match(picker, /<WorkspaceChoiceList/)
assert.match(picker, /@select="selectWorkspace"/)
assert.match(choiceList, /'选择已有项目'/)
assert.match(choiceList, /'添加新项目…'/)
assert.match(appShell, /function selectBoundProject\(key: string, workspace: DshWorkspace\)/)
assert.match(appShell, /@select-code-workspace="\(workspace\) => selectBoundProject\(pane\.key, workspace\)"/)
assert.match(appShell, /@choose-code-workspace="\(\) => openProjectCreate\(pane\.key\)"/)

console.log('new-session project context tests passed')
