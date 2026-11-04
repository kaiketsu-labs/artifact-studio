/* Local source editing, independent of authored artifact execution. */
import {basicSetup} from 'codemirror';
import {EditorState, Compartment} from '@codemirror/state';
import {EditorView, keymap} from '@codemirror/view';
import {indentWithTab} from '@codemirror/commands';
import {HighlightStyle,syntaxHighlighting} from '@codemirror/language';
import {tags} from '@lezer/highlight';
import {openSearchPanel} from '@codemirror/search';
import {markdown} from '@codemirror/lang-markdown';
import {javascript} from '@codemirror/lang-javascript';
import {html} from '@codemirror/lang-html';
import {css} from '@codemirror/lang-css';
import {json} from '@codemirror/lang-json';
import {yaml} from '@codemirror/lang-yaml';

const modes={md:['Markdown',markdown],markdown:['Markdown',markdown],mdown:['Markdown',markdown],mkd:['Markdown',markdown],js:['JavaScript',javascript],mjs:['JavaScript',javascript],ts:['TypeScript',()=>javascript({typescript:true})],jsx:['JSX',()=>javascript({jsx:true})],tsx:['TSX',()=>javascript({jsx:true,typescript:true})],html:['HTML',html],htm:['HTML',html],css:['CSS',css],json:['JSON',json],yaml:['YAML',yaml],yml:['YAML',yaml]};

/** Create a bounded, accessible code editor with familiar editing shortcuts. */
export function create(parent,{value,name,onChange,onSave,onPosition}){
  const suffix=name.split('.').at(-1).toLowerCase(),mode=modes[suffix],writable=new Compartment();
  const editor=new EditorView({parent,doc:value,extensions:[
    basicSetup,...(mode?[mode[1]()]:[]),...(!mode||mode[0]==='Markdown'?[EditorView.lineWrapping]:[]),
    syntaxHighlighting(HighlightStyle.define([
      {tag:tags.keyword,color:'var(--code-keyword)'},{tag:[tags.number,tags.atom],color:'var(--code-number)'},
      {tag:tags.string,color:'var(--code-string)'},{tag:tags.comment,color:'var(--code-comment)',fontStyle:'italic'},
      {tag:[tags.typeName,tags.tagName],color:'var(--code-type)'},{tag:[tags.propertyName,tags.attributeName],color:'var(--code-property)'},
      {tag:tags.heading,color:'var(--code-type)',fontWeight:'bold'},{tag:tags.link,color:'var(--code-property)',textDecoration:'underline'}
    ])),
    writable.of([EditorState.readOnly.of(false),EditorView.editable.of(true)]),
    EditorView.contentAttributes.of({'aria-label':`Edit ${name}`,'spellcheck':'false','autocapitalize':'off'}),
    EditorState.transactionFilter.of(transaction=>transaction.newDoc.length>1024*1024?[]:transaction),
    keymap.of([{key:'Mod-s',run:()=>{onSave();return true;}},indentWithTab]),
    EditorView.updateListener.of(update=>{
      if(update.docChanged)onChange();
      if(update.docChanged||update.selectionSet){const head=update.state.selection.main.head,line=update.state.doc.lineAt(head);onPosition(line.number,head-line.from+1);}
    }),
    EditorView.theme({
      '&':{height:'100%',color:'var(--hub-ink)',backgroundColor:'var(--hub-panel)'},
      '.cm-scroller':{fontFamily:'ui-monospace,SFMono-Regular,monospace',fontSize:'13px',lineHeight:'1.7',overflow:'auto'},
      '.cm-content':{padding:'12px 0',caretColor:'var(--hub-ink)'},
      '.cm-gutters':{borderRight:'1px solid var(--hub-line)',backgroundColor:'var(--hub-bg)',color:'var(--hub-muted)'},
      '.cm-line':{padding:'0 14px'},
      '&.cm-focused':{outline:'none'},
      '.cm-activeLine,.cm-activeLineGutter':{backgroundColor:'var(--code-active)'},
      '&.cm-focused .cm-selectionBackground,.cm-selectionBackground,::selection':{backgroundColor:'var(--code-selection) !important'},
      '.cm-panels':{backgroundColor:'var(--hub-bg)',color:'var(--hub-ink)',borderColor:'var(--hub-line)'},
      '.cm-search':{padding:'8px',fontSize:'12px'},
      '.cm-search input':{backgroundColor:'var(--hub-panel)',color:'var(--hub-ink)',borderColor:'var(--hub-line)'},
      '.cm-cursor':{borderLeftColor:'var(--hub-ink)'},
      '.cm-foldPlaceholder':{backgroundColor:'var(--hub-bg)',borderColor:'var(--hub-line)',color:'var(--hub-muted)'}
    })
  ]});
  onPosition(1,1);
  return {value:()=>editor.state.doc.toString(),language:mode?.[0]||'Plain text',focus:()=>editor.focus(),find:()=>openSearchPanel(editor),destroy:()=>editor.destroy(),setReadOnly:readOnly=>editor.dispatch({effects:writable.reconfigure([EditorState.readOnly.of(readOnly),EditorView.editable.of(!readOnly)])})};
}
