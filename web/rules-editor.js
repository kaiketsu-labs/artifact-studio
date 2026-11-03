/* Rich workspace-rule editing with Markdown as the canonical saved representation. */
((root)=>{
  /** Retain source editing whenever rich text cannot represent the complete document. */
  function needsSource(tokens){
    const supported=new Set(['space','heading','paragraph','text','strong','em','codespan','code','blockquote','list','list_item','link','escape','br']);
    return tokens.some(token=>!supported.has(token.type)||token.task||token.lang||token.tokens&&needsSource(token.tokens)||token.items&&needsSource(token.items));
  }
  let dependencies=null;
  function asset(tag,attributes){return new Promise((resolve,reject)=>{const node=document.createElement(tag);Object.assign(node,attributes);node.onload=resolve;node.onerror=()=>{node.remove();reject(new Error('The rules editor could not load. Try again.'));};document.head.append(node);});}
  function load(){
    if(!dependencies)dependencies=Promise.all([
      asset('script',{src:'/web/vendor/quill-2.0.3.js'}),asset('link',{rel:'stylesheet',href:'/web/vendor/quill-2.0.3.css'}),
      root.marked?Promise.resolve():asset('script',{src:'/web/vendor/marked-18.1.2.js'}),asset('script',{src:'/web/vendor/turndown-7.2.4.js'})
    ]).catch(error=>{dependencies=null;throw error;});
    return dependencies;
  }
  async function create(container,{onChange=()=>{},readOnly=false,label='Workspace rules'}={}){
    await load();
    const editor=new Quill(container,{theme:'snow',readOnly,formats:['header','bold','italic','list','blockquote','code','code-block','link'],modules:{toolbar:readOnly?false:[[{header:[1,2,false]}],['bold','italic'],[{list:'bullet'},{list:'ordered'}],['blockquote','code-block','link','clean']],history:{userOnly:true}}});
    const converter=new TurndownService({headingStyle:'atx',bulletListMarker:'-',codeBlockStyle:'fenced'});
    converter.addRule('quill-code-block',{filter:'pre',replacement:(_content,node)=>'\n\n```\n'+node.textContent.replace(/\n$/,'')+'\n```\n\n'});
    let source='',dirty=false,sourceMode=false,enabled=!readOnly,generation=0,voice=null,sourceVoice=null;
    const sourceField=document.createElement('textarea');sourceField.className='rules-source';sourceField.hidden=true;sourceField.readOnly=readOnly;sourceField.setAttribute('aria-label',`${label} Markdown`);container.after(sourceField);
    if(readOnly)sourceField.setAttribute('data-voice-exclude','');
    const sourceNotice=document.createElement('p');sourceNotice.className='rules-source-notice';sourceNotice.hidden=true;sourceNotice.textContent='Markdown preserves the formatting in this document.';sourceField.before(sourceNotice);
    sourceField.addEventListener('input',()=>{dirty=true;onChange();});
    editor.root.setAttribute('aria-label',label);editor.root.setAttribute('role','textbox');editor.root.setAttribute('aria-multiline','true');
    if(readOnly)editor.root.setAttribute('aria-readonly','true');
    const toolbar=editor.getModule('toolbar')?.container;
    if(toolbar){
      toolbar.setAttribute('role','toolbar');toolbar.setAttribute('aria-label','Text formatting');
      for(const [selector,label] of [['.ql-bold','Bold'],['.ql-italic','Italic'],['.ql-list[value="bullet"]','Bulleted list'],['.ql-list[value="ordered"]','Numbered list'],['.ql-blockquote','Quote'],['.ql-code-block','Code block'],['.ql-link','Link'],['.ql-clean','Clear formatting']]){const button=toolbar.querySelector(selector);if(button){button.type='button';button.title=label;button.setAttribute('aria-label',label);}}
      toolbar.querySelector('.ql-picker-label')?.setAttribute('aria-label','Text style');
    }
    editor.on('text-change',(_delta,_old,origin)=>{if(origin==='user'){dirty=true;onChange();}});
    function bindVoice(){
      if(readOnly||!root.slideVoice)return;
      sourceVoice=root.slideVoice.register(sourceField);
      if(voice)return;
      voice=root.slideVoice.register(editor.root,{
        label,controlsHost:toolbar||container,visible:()=>enabled&&!sourceMode,
        capture:()=>({generation,value:editor.getText(),range:editor.getSelection()||{index:Math.max(0,editor.getLength()-1),length:0}}),
        available:context=>enabled&&!sourceMode&&editor.root.isConnected&&context.generation===generation&&editor.getText()===context.value,
        insert(text,context){
          if(!enabled||sourceMode||context.generation!==generation||editor.getText()!==context.value)return false;
          const {index,length}=context.range,before=editor.getText(0,index),after=editor.getText(index+length);
          const insertion=(before&&!/\s$/.test(before)&&! /^[,.;:!?)]/.test(text)?' ':'')+text+(after&&!/^\s|^[,.;:!?)]/.test(after)?' ':'');
          if(editor.getLength()-1-length+insertion.length>20000)return false;
          if(length)editor.deleteText(index,length,'user');
          editor.insertText(index,insertion,'user');
          context.value=editor.getText();
          context.range={index:index+insertion.length,length:0};
          editor.setSelection(context.range.index,0,'user');return true;
        }
      });
    }
    function cancelVoice(){bindVoice();voice?.cancel();sourceVoice?.cancel();}
    bindVoice();
    return {
      setMarkdown(markdown){cancelVoice();generation++;source=markdown;dirty=false;sourceMode=needsSource(marked.lexer(markdown));sourceField.value=markdown;sourceField.hidden=!sourceMode;sourceNotice.hidden=!sourceMode;container.hidden=sourceMode;if(toolbar)toolbar.hidden=sourceMode;if(!sourceMode){const delta=editor.clipboard.convert({html:marked.parse(markdown,{async:false})});editor.setContents(delta,'silent');editor.history.clear();}},
      getMarkdown(){return sourceMode?sourceField.value:dirty?converter.turndown(editor.getSemanticHTML()).replace(/\u00a0/g,' ')+'\n':source;},
      enable(value){if(!value)cancelVoice();enabled=value&&!readOnly;editor.enable(enabled);sourceField.disabled=!value;if(toolbar)toolbar.querySelectorAll('button,select').forEach(control=>{if(control!==voice?.button)control.disabled=!value;});if(voice)voice.controls.hidden=!enabled;},
      focus(){if(sourceMode)sourceField.focus();else editor.focus();}
    };
  }
  const api={create,needsSource};
  if(typeof module!=='undefined')module.exports=api;else root.SlideRulesEditor=api;
})(typeof window==='undefined'?{}:window);
