limits => {
  'use strict';
  const {maxBytes, maxObjects} = limits;
  const slide = document.querySelector('#capture-stage > .slide');
  if (!slide) return {error:'The slide article is missing.'};
  const canvas = slide.getBoundingClientRect();
  const colorContext = document.createElement('canvas').getContext('2d');
  const px = value => parseFloat(value) || 0;
  const normal = (value, defaultValue = 'none') => value && value !== defaultValue;
  const same = (a, b, tolerance = 0.03) => Math.abs(a - b) < tolerance;
  const label = element => element.getAttribute('data-element-id') || element.id || element.localName;
  const unsupported = (element, feature) => {
    throw new Error(`${label(element)} uses ${feature}, which has no supported editable PowerPoint equivalent.`);
  };
  const base = (type, rect) => ({
    type, x:rect.left - canvas.left, y:rect.top - canvas.top,
    width:rect.width, height:rect.height,
  });
  const asRect = (left, top, width, height) => ({left, top, width, height, right:left + width, bottom:top + height});
  const intersection = (a, b) => asRect(
    Math.max(a.left, b.left), Math.max(a.top, b.top),
    Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)),
    Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)),
  );
  const contained = (rect, clip) => (
    rect.left >= clip.left - 0.75 && rect.top >= clip.top - 0.75 &&
    rect.right <= clip.right + 0.75 && rect.bottom <= clip.bottom + 0.75
  );
  const invisible = (rect, clip) => {
    const cut = intersection(rect, clip);
    return !cut.width || !cut.height;
  };
  function color(value, opacity = 1) {
    if (value === 'none' || !value) return 'transparent';
    colorContext.clearRect(0, 0, 1, 1);
    colorContext.fillStyle = value;
    colorContext.globalAlpha = opacity;
    colorContext.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = colorContext.getImageData(0, 0, 1, 1).data;
    return a === 0 ? 'transparent' : a === 255 ? `rgb(${r},${g},${b})` : `rgba(${r},${g},${b},${a / 255})`;
  }
  function separated(value) {
    const parts = [];
    let depth = 0, start = 0, quote = '';
    for (let i = 0; i < value.length; i++) {
      const character = value[i];
      if (quote) {
        if (character === quote && value[i - 1] !== '\\') quote = '';
      } else if (character === '"' || character === "'") quote = character;
      else if (character === '(') depth++;
      else if (character === ')') depth--;
      else if (character === ',' && !depth) {
        parts.push(value.slice(start, i).trim());
        start = i + 1;
      }
    }
    parts.push(value.slice(start).trim());
    return parts;
  }
  function background(element, style, opacity) {
    const fill = color(style.backgroundColor, opacity);
    if (style.backgroundImage === 'none') return {fill};
    const match = /^linear-gradient\((.*)\)$/.exec(style.backgroundImage);
    if (!match) return {fill, source:/^url\("?(data:[^"]+?)"?\)$/.exec(style.backgroundImage)?.[1]};
    const parts = separated(match[1]);
    let angle = 180;
    if (/^-?[\d.]+deg$/.test(parts[0])) angle = px(parts.shift());
    else if (parts[0].startsWith('to ')) {
      const directions = {'to top':0, 'to right':90, 'to bottom':180, 'to left':270};
      angle = directions[parts.shift()];
    }
    if (!Number.isFinite(angle) || parts.length < 2) unsupported(element, 'a complex background gradient');
    const stops = parts.map(part => {
      const match = /^(.*?)(?:\s+(-?[\d.]+)%)?$/.exec(part);
      if (!match || !CSS.supports('color', match[1])) unsupported(element, 'a complex gradient stop');
      return {color:color(match[1], opacity), position:match[2] === undefined ? null : px(match[2]) / 100};
    });
    stops[0].position ??= 0;
    stops.at(-1).position ??= 1;
    let start = 0;
    for (let end = 1; end < stops.length; end++) {
      if (stops[end].position === null) continue;
      stops[end].position = Math.max(stops[end].position, stops[start].position);
      for (let index = start + 1; index < end; index++) {
        stops[index].position = stops[start].position +
          (stops[end].position - stops[start].position) * (index - start) / (end - start);
      }
      start = end;
    }
    if (stops.some(stop => stop.position < 0 || stop.position > 1)) unsupported(element, 'gradient stops outside its bounds');
    return {fill, gradient:{angle, stops}};
  }
  function checkEffects(element, style) {
    for (const [property, value] of [
      ['a CSS mask',style.maskImage], ['a CSS clip path',style.clipPath],
      ['a filter',style.filter], ['a backdrop filter',style.backdropFilter],
      ['a blend mode',style.mixBlendMode === 'normal' ? 'none' : style.mixBlendMode],
      ['a CSS box shadow',style.boxShadow], ['a text shadow',style.textShadow],
      ['a perspective transform',style.perspective],
      ['a CSS outline',style.outlineStyle], ['a border image',style.borderImageSource],
    ]) {
      if (normal(value)) unsupported(element, property);
    }
    for (const pseudo of ['::before', '::after']) {
      const generated = getComputedStyle(element, pseudo);
      if (generated.display !== 'none' && generated.visibility !== 'hidden' &&
          normal(generated.content) && generated.content !== 'normal') {
        unsupported(element, `generated ${pseudo} content`);
      }
    }
  }
  function checkMotion() {
    const authoredMotion = [
      ['[data-build], [data-auto-reveal]', 'a Studio reveal step'],
      ['[data-animate-grow="x"]', 'a Studio growth animation'],
      ['[data-count-to]', 'a Studio counter animation'],
    ];
    for (const [selector, feature] of authoredMotion) {
      const object = slide.matches(selector) ? slide : slide.querySelector(selector);
      if (object) unsupported(object, feature);
    }
    for (const animation of document.getAnimations()) {
      const target = animation.effect?.target;
      if (target instanceof Element) unsupported(target, 'an active CSS animation or transition');
    }
    const elements = [document.documentElement, document.body, document.getElementById('capture-stage'),
      slide, ...slide.querySelectorAll('*')];
    if (elements.length > 6004) unsupported(slide, 'more than 6,000 HTML elements');
    for (const element of elements) {
      if (['animate','animateMotion','animateTransform','set'].includes(element.localName)) {
        unsupported(element, 'an SVG animation');
      }
      for (const pseudo of [null, '::before', '::after']) {
        const style = getComputedStyle(element, pseudo);
        if (pseudo && (!normal(style.content) || style.content === 'normal')) continue;
        if (separated(style.animationName).some(name => name !== 'none')) unsupported(element, 'a CSS keyframe animation');
      }
    }
  }
  function transform(element, style, inherited) {
    if (normal(style.scale) || normal(style.rotate)) unsupported(element, 'a CSS individual scale or rotate');
    if (style.transform === 'none') return inherited;
    const matrix = new DOMMatrix(style.transform);
    const scaleX = Math.hypot(matrix.a, matrix.b), scaleY = Math.hypot(matrix.c, matrix.d);
    if (!matrix.is2D || !same(matrix.a * matrix.c + matrix.b * matrix.d, 0) ||
        !same(scaleX, scaleY) || matrix.a * matrix.d - matrix.b * matrix.c <= 0) {
      unsupported(element, 'a skew, reflection, non-uniform scale, or 3D transform');
    }
    return {angle:inherited.angle + Math.atan2(matrix.b, matrix.a) * 180 / Math.PI,
      scale:inherited.scale * scaleX};
  }
  function geometry(element, rect, transformState) {
    if (same(transformState.angle, 0)) return {rect};
    const width = element.offsetWidth * transformState.scale;
    const height = element.offsetHeight * transformState.scale;
    return {
      rect:asRect(rect.left + (rect.width - width) / 2, rect.top + (rect.height - height) / 2, width, height),
      rotation:transformState.angle,
    };
  }
  const sides = ['Top', 'Right', 'Bottom', 'Left'];
  function border(style, opacity, scale, element) {
    return sides.map(side => {
      const width = px(style[`border${side}Width`]) * scale;
      const stroke = color(style[`border${side}Color`], opacity);
      if (!width || stroke === 'transparent') return {stroke:'transparent', strokeWidth:0};
      if (style[`border${side}Style`] !== 'solid') unsupported(element, 'a non-solid border');
      return {stroke, strokeWidth:width};
    });
  }
  function rounded(element, rect, style, scale) {
    const values = ['TopLeft', 'TopRight', 'BottomRight', 'BottomLeft'].map(corner => {
      const [x, y = x] = style[`border${corner}Radius`].split(/\s+/);
      const size = (value, length) => value.endsWith('%') ? px(value) * length / 100 : px(value) * scale;
      return [size(x, rect.width), size(y, rect.height)];
    });
    if (values.every(([x,y]) => !x && !y)) return {shape:'rect'};
    if (values.every(([x,y]) => x >= rect.width / 2 - 0.1 && y >= rect.height / 2 - 0.1)) return {shape:'ellipse'};
    const [radius] = values[0];
    if (!values.every(([x,y]) => same(radius, x) && same(radius, y))) unsupported(element, 'asymmetric rounded corners');
    return {shape:'roundRect', radius};
  }
  function line(x, y, x2, y2, stroke, strokeWidth) {
    return {type:'line', x:x - canvas.left, y:y - canvas.top, width:Math.abs(x2-x), height:Math.abs(y2-y),
      x2:x2-canvas.left, y2:y2-canvas.top, stroke, strokeWidth};
  }
  function paintBox(element, rect, style, opacity, transformState, clip, paint) {
    const bg = background(element, style, opacity);
    if (normal(style.backgroundImage) && !bg.gradient && !bg.source) unsupported(element, 'a loaded, tiled or complex CSS background image');
    if (style.backgroundClip === 'text' || normal(style.backgroundBlendMode, 'normal')) unsupported(element, 'CSS background clipping or blending');
    const outline = border(style, opacity, transformState.scale, element);
    const uniform = outline.every(side => JSON.stringify(side) === JSON.stringify(outline[0]));
    if (!bg.gradient && !bg.source && bg.fill === 'transparent' && !outline.some(side => side.strokeWidth)) return;
    const {rect:box, rotation} = geometry(element, rect, transformState);
    const shape = rounded(element, box, style, transformState.scale);
    if (!contained(rect, clip)) unsupported(element, 'a partially clipped shape');
    if (!uniform && (rotation || shape.shape !== 'rect')) unsupported(element, 'different borders on a rotated or rounded box');
    const stroke = uniform ? outline[0] : {};
    if (!bg.source) {
      if (bg.fill !== 'transparent' || bg.gradient || uniform) {
        paint({...base('shape', box), rotation, ...shape, fill:bg.fill, gradient:bg.gradient, ...stroke});
      }
    } else {
      if (shape.shape !== 'rect' || rotation || opacity !== 1) unsupported(element, 'a rounded, translucent, or rotated background image');
      if (bg.fill !== 'transparent') paint({...base('shape', box), fill:bg.fill});
      const image = new Image();
      image.src = bg.source;
      return image.decode().catch(() => unsupported(element, 'an unreadable background image')).then(() => {
        const parts = style.backgroundSize.split(' ');
        const validSize = ['cover', 'contain'].includes(parts[0]) || parts.every(part => part === 'auto' || /^[\d.]+(?:px|%)$/.test(part));
        if (!validSize || style.backgroundAttachment !== 'scroll') unsupported(element, 'unsupported background sizing');
        const sizing = ['cover', 'contain'].includes(parts[0]) ? {fit:parts[0]} : {sizes:parts};
        const object = imageLayout(element, bg.source, box, clip, image.naturalWidth, image.naturalHeight,
          style.backgroundPosition, sizing, transformState.scale);
        if (style.backgroundRepeat !== 'no-repeat' && contained(object._painted, box) &&
            (object._painted.width < box.width - 0.75 || object._painted.height < box.height - 0.75)) {
          unsupported(element, 'a repeating background image');
        }
        delete object._painted;
        paint(object);
        if (uniform && stroke.strokeWidth) paint({...base('shape', box), ...stroke});
        if (!uniform) paintBorders(box, outline, paint);
      });
    }
    if (!uniform) paintBorders(box, outline, paint);
  }
  function paintBorders(box, outline, paint) {
    const coords = [
      w => [box.left,box.top+w/2,box.right,box.top+w/2],
      w => [box.right-w/2,box.top,box.right-w/2,box.bottom],
      w => [box.left,box.bottom-w/2,box.right,box.bottom-w/2],
      w => [box.left+w/2,box.top,box.left+w/2,box.bottom],
    ];
    outline.forEach(({stroke, strokeWidth}, index) => {
      if (strokeWidth) paint(line(...coords[index](strokeWidth), stroke, strokeWidth));
    });
  }
  function objectPosition(element, position, available, scale) {
    if (position.endsWith('%')) return available * px(position) / 100;
    if (position.endsWith('px')) return px(position) * scale;
    const match = /^calc\((-?[\d.]+)% ([+-]) ([\d.]+)px\)$/.exec(position);
    if (match) return available * px(match[1]) / 100 + (match[2] === '-' ? -1 : 1) * px(match[3]) * scale;
    return unsupported(element, 'a complex image position');
  }
  function imageLayout(element, data, box, clip, naturalW, naturalH, position, sizing, scale) {
    if (!/^data:image\/(?:png|jpeg|gif|bmp|tiff|svg\+xml|webp)(?:[;,])/i.test(data) || !naturalW || !naturalH) {
      unsupported(element, 'an unavailable or unsupported image');
    }
    let w = box.width, h = box.height;
    if (sizing.sizes) {
      const [width, height = 'auto'] = sizing.sizes;
      const dimension = (value, length) => value === 'auto' ? null : value.endsWith('%') ? length * px(value) / 100 : px(value) * scale;
      w = dimension(width, box.width); h = dimension(height, box.height);
      if (w === null && h === null) { w = naturalW * scale; h = naturalH * scale; }
      else if (w === null) w = h * naturalW / naturalH;
      else if (h === null) h = w * naturalH / naturalW;
    } else if (sizing.fit !== 'fill') {
      const ratios = [w / naturalW, h / naturalH];
      const native = scale;
      const ratio = sizing.fit === 'cover' ? Math.max(...ratios) :
        sizing.fit === 'none' ? native : sizing.fit === 'scale-down' ? Math.min(native, ...ratios) : Math.min(...ratios);
      w = naturalW * ratio; h = naturalH * ratio;
    }
    const positions = position.match(/calc\([^)]*\)|[^ ]+/g) || [];
    if (positions.length !== 2) unsupported(element, 'a complex image position');
    const painted = asRect(box.left + objectPosition(element, positions[0], box.width - w, scale),
      box.top + objectPosition(element, positions[1], box.height - h, scale), w, h);
    const visible = intersection(intersection(painted, box), clip);
    if (!visible.width || !visible.height) unsupported(element, 'an image positioned outside its box');
    return {...base('image', visible), data, _painted:painted, crop:{
      left:(visible.left-painted.left)/w, right:(painted.right-visible.right)/w,
      top:(visible.top-painted.top)/h, bottom:(painted.bottom-visible.bottom)/h,
    }};
  }
  function imageObject(element, rect, style, opacity, state, clip) {
    if (opacity !== 1 || rounded(element, rect, style, state.scale).shape !== 'rect') {
      unsupported(element, 'a translucent or nonrectangular picture mask');
    }
    const {rect:box, rotation} = geometry(element, rect, state);
    const inset = sides.map(side => (px(style[`padding${side}`]) + px(style[`border${side}Width`])) * state.scale);
    if (rotation && (inset.some(Boolean) || !contained(rect, clip))) unsupported(element, 'a rotated image with padding or clipping');
    const content = asRect(box.left + inset[3], box.top + inset[0], box.width - inset[1] - inset[3], box.height - inset[0] - inset[2]);
    const image = imageLayout(element, element.currentSrc || element.src, content, rotation ? content : clip,
      element.naturalWidth, element.naturalHeight, style.objectPosition, {fit:style.objectFit}, state.scale);
    if (rotation && (!same(image.width, content.width) || !same(image.height, content.height))) unsupported(element, 'a rotated image with an inset fit');
    delete image._painted;
    return {...image, rotation};
  }
  const fontNames = {'system-ui':'Arial', 'sans-serif':'Arial', 'serif':'Times New Roman', 'monospace':'Courier New'};
  function textStyle(element, style, opacity, scale, decorations) {
    if (style.writingMode !== 'horizontal-tb' || style.direction !== 'ltr') unsupported(element, 'non-horizontal or right-to-left text');
    if (normal(style.webkitTextStrokeWidth, '0px')) unsupported(element, 'outlined text');
    const font = style.fontFamily.split(',')[0].trim().replace(/^['"]|['"]$/g,'');
    return {
      fontFamily:fontNames[font] || font, fontSize:px(style.fontSize) * scale,
      color:color(style.webkitTextFillColor || style.color, opacity),
      bold:Number(style.fontWeight) >= 600, italic:/^(italic|oblique)/.test(style.fontStyle),
      underline:decorations.includes('underline'), strike:decorations.includes('line-through'),
      letterSpacing:px(style.letterSpacing) * scale,
    };
  }
  function displayText(text, style, previous) {
    if (style.textTransform === 'uppercase') return text.toLocaleUpperCase();
    if (style.textTransform === 'lowercase') return text.toLocaleLowerCase();
    if (style.textTransform === 'capitalize') return /[\p{L}\p{N}]/u.test(previous) ? text : text.toLocaleUpperCase();
    return text;
  }
  const segmenter = new Intl.Segmenter(undefined, {granularity:'grapheme'});
  let characters = 0;
  function addText(node, style, opacity, state, decorations, lines, clip) {
    if (!node.data.trim() && !lines.length) return;
    if (style.visibility !== 'visible' || opacity <= 0) return;
    const owner = node.parentElement;
    const run = textStyle(owner, style, opacity, state.scale, decorations);
    if (run.color === 'transparent') return;
    if (!same(state.angle, 0)) unsupported(owner, 'rotated text');
    if (style.textOverflow !== 'clip' || normal(style.webkitLineClamp)) unsupported(owner, 'truncated text');
    if (!['none','uppercase','lowercase','capitalize'].includes(style.textTransform)) unsupported(owner, 'a special text transformation');
    characters += node.length;
    if (characters > 24000) unsupported(slide, 'more than 24,000 text characters');
    const range = document.createRange();
    const key = JSON.stringify(run);
    for (const {segment, index} of segmenter.segment(node.data)) {
      range.setStart(node, index);
      range.setEnd(node, index + segment.length);
      const rect = range.getBoundingClientRect();
      if (rect.width < 0.05 || rect.height <= 0 || invisible(rect, clip)) continue;
      if (!contained(rect, clip)) unsupported(owner, 'partially clipped text');
      const glyph = displayText(segment, style, node.data[index - 1] || '');
      const lineHeight = px(style.lineHeight) * state.scale || rect.height;
      const mid = rect.top + rect.height / 2;
      let row = lines.at(-1);
      if (!row || Math.abs(row.mid - mid) > Math.max(2, Math.min(rect.height, row.height) * 0.48) ||
          rect.left + 1 < row.lastLeft) {
        row = {mid, y:rect.top + (rect.height - lineHeight) / 2, height:lineHeight, glyphs:[],
          left:rect.left, right:rect.right, lastLeft:rect.left};
        lines.push(row);
      }
      row.lastLeft = rect.left;
      row.left = Math.min(row.left, rect.left);
      row.right = Math.max(row.right, rect.right);
      const top = rect.top + (rect.height - lineHeight) / 2;
      row.height = Math.max(row.y + row.height, top + lineHeight) - Math.min(row.y, top);
      row.y = Math.min(row.y, top);
      const previous = row.glyphs.at(-1);
      if (previous?.key === key) previous.text += glyph;
      else row.glyphs.push({...run, text:glyph, key});
    }
  }
  function paragraph(row, style) {
    const alignment = style.textAlign === 'start' ? 'left' : style.textAlign === 'end' ? 'right' : style.textAlign;
    return {runs:row.glyphs.map(({key, ...run}) => run), align:alignment, lineHeight:row.height};
  }
  function textObjects(element, lines, style, paint) {
    if (!lines.length) return;
    const lineGap = lines[1]?.y - lines[0].y;
    const align = style.textAlign;
    const edge = row => align === 'center' ? (row.left + row.right) / 2 :
      ['right', 'end'].includes(align) ? row.right : row.left;
    const grouped = lines.length === 1 || lines.every((row, index) =>
      same(edge(row), edge(lines[0]), 1.5) && (index === 0 || same(row.y - lines[index-1].y, lineGap, 1)));
    const text = group => {
      const left = Math.min(...group.map(row => row.left));
      const width = Math.max(...group.map(row => row.right)) - left;
      const top = group[0].y;
      const height = Math.max(...group.map(row => row.y + row.height)) - top;
      return {...base('text', asRect(left, top, width, height)), name:label(element), verticalAlign:'top',
        paragraphs:group.map((row, index) => ({...paragraph(row, style),
          align:group.length === 1 ? 'left' : paragraph(row, style).align,
          spaceAfter:group[index + 1] ? Math.max(0, group[index + 1].y - row.y - row.height) : 0}))};
    };
    if (grouped) paint(text(lines));
    else lines.forEach(row => paint(text([row])));
  }
  function tableObject(element, rect, style, opacity, state, clip) {
    if (state.angle || opacity !== 1 || !contained(rect, clip)) unsupported(element, 'a rotated, translucent, or clipped table');
    if (element.caption) unsupported(element, 'a table caption');
    const [gapX, gapY] = style.borderCollapse === 'collapse' ? [0,0] : style.borderSpacing.split(' ').map(value => px(value) * state.scale);
    const rowElements = Array.from(element.rows);
    if (!rowElements.length) unsupported(element, 'an empty table');
    const rowHeights = rowElements.map(row => row.getBoundingClientRect().height);
    const columnWidths = Array.from(rowElements[0].cells, cell => cell.getBoundingClientRect().width);
    const rows = rowElements.map(row => {
      if (row.cells.length !== columnWidths.length) unsupported(element, 'irregular table rows');
      const rowStyle = getComputedStyle(row);
      const sectionStyle = getComputedStyle(row.parentElement);
      return Array.from(row.cells, (cell, column) => {
        if (cell.colSpan !== 1 || cell.rowSpan !== 1) unsupported(cell, 'merged table cells');
        const computed = getComputedStyle(cell);
        checkEffects(cell, computed);
        const bounds = cell.getBoundingClientRect();
        if (!same(bounds.width, columnWidths[column], 1) || normal(computed.backgroundImage) || normal(computed.transform)) {
          unsupported(cell, 'complex table-cell styling');
        }
        const outlines = border(computed, 1, state.scale, cell);
        const borders = Object.fromEntries(sides.map((side,index) => [side.toLowerCase(),outlines[index]]));
        const lines = [];
        function tableText(parent, style, decorations) {
          for (const node of parent.childNodes) {
            if (node.nodeType === Node.TEXT_NODE) addText(node, style, 1, state, decorations, lines, clip);
            else if (node instanceof HTMLElement) {
              if (node.localName === 'br') continue;
              if (!inlineTextTags.has(node.localName)) unsupported(node, 'non-text content in a table cell');
              const childStyle = getComputedStyle(node);
              checkEffects(node, childStyle);
              tableText(node, childStyle, decorations + ' ' + childStyle.textDecorationLine);
            }
          }
        }
        tableText(cell, computed, computed.textDecorationLine);
        const fill = [computed, rowStyle, sectionStyle, style].map(item => color(item.backgroundColor)).find(item => item !== 'transparent') || 'transparent';
        if ((gapX || gapY) && (fill !== 'transparent' || outlines.some(side => side.strokeWidth))) {
          unsupported(cell, 'visible spacing between painted table cells; use border-collapse: collapse');
        }
        const padding = Object.fromEntries(sides.map((side,index) =>
          [side.toLowerCase(), px(computed[`padding${side}`]) * state.scale + (index % 2 ? gapX : gapY) / 2]));
        return {paragraphs:lines.map(row => paragraph(row, computed)), fill, padding,
          verticalAlign:computed.verticalAlign, borders};
      });
    });
    const tableRect = asRect(rect.left+gapX/2,rect.top+gapY/2,rect.width-gapX,rect.height-gapY);
    return {...base('table', tableRect), rows, columnWidths:columnWidths.map(width => width+gapX),
      rowHeights:rowHeights.map(height => height+gapY)};
  }
  function pathCommands(element, mapped) {
    const source = getComputedStyle(element).d;
    if (source === 'none') return [];
    const data = /^path\("([^"]*)"\)$/.exec(source)?.[1] || element.getAttribute('d') || '';
    const tokens = data.match(/[a-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:e[-+]?\d+)?/gi) || [];
    const counts = {M:2,L:2,H:1,V:1,C:6,S:4,Q:4,T:2,Z:0};
    const commands = [];
    let index = 0, command, x = 0, y = 0, startX = 0, startY = 0;
    let previous = '', controlX = 0, controlY = 0;
    while (index < tokens.length) {
      if (/^[a-z]$/i.test(tokens[index])) command = tokens[index++];
      const kind = command?.toUpperCase();
      if (!(kind in counts) || tokens.length - index < counts[kind]) unsupported(element, 'unsupported SVG path commands');
      const values = tokens.slice(index, index + counts[kind]).map(Number);
      if (values.some(value => !Number.isFinite(value))) unsupported(element, 'invalid SVG path coordinates');
      index += counts[kind];
      const relative = command !== kind;
      const getX = i => values[i] + (relative ? x : 0);
      const getY = i => values[i] + (relative ? y : 0);
      let point, one, two, nextX, nextY;
      switch (kind) {
        case 'M':
          x = getX(0); y = getY(1);
          startX = x; startY = y;
          point = mapped(x,y);
          commands.push({x:point.x, y:point.y, move:true});
          command = relative ? 'l' : 'L';
          break;
        case 'H':
        case 'V':
        case 'L':
          nextX = kind === 'V' ? x : getX(0);
          nextY = kind === 'H' ? y : getY(kind === 'V' ? 0 : 1);
          x = nextX; y = nextY;
          point = mapped(x,y);
          commands.push({x:point.x,y:point.y});
          break;
        case 'C':
        case 'S':
          one = kind === 'C' ? mapped(getX(0),getY(1)) :
            ['C','S'].includes(previous) ? mapped(2*x-controlX,2*y-controlY) : mapped(x,y);
          controlX = getX(kind === 'C' ? 2 : 0);
          controlY = getY(kind === 'C' ? 3 : 1);
          two = mapped(controlX,controlY);
          nextX = getX(kind === 'C' ? 4 : 2);
          nextY = getY(kind === 'C' ? 5 : 3);
          point = mapped(nextX,nextY);
          commands.push({x:point.x,y:point.y,x1:one.x,y1:one.y,x2:two.x,y2:two.y});
          x = nextX; y = nextY;
          break;
        case 'Q':
        case 'T':
          controlX = kind === 'Q' ? getX(0) : ['Q','T'].includes(previous) ? 2*x-controlX : x;
          controlY = kind === 'Q' ? getY(1) : ['Q','T'].includes(previous) ? 2*y-controlY : y;
          nextX = getX(kind === 'Q' ? 2 : 0);
          nextY = getY(kind === 'Q' ? 3 : 1);
          point = mapped(nextX,nextY);
          one = mapped((x+2*controlX)/3,(y+2*controlY)/3);
          two = mapped((nextX+2*controlX)/3,(nextY+2*controlY)/3);
          commands.push({x:point.x,y:point.y,x1:one.x,y1:one.y,x2:two.x,y2:two.y});
          x = nextX; y = nextY;
          break;
        case 'Z':
          commands.push({close:true});
          x = startX; y = startY;
          command = undefined;
      }
      previous = kind;
    }
    return commands;
  }
  function freeform(box, commands, fill, stroke, strokeWidth) {
    const path = commands.map(command => {
      if (command.close) return command;
      const {x,y,x1,y1,x2,y2,move} = command;
      return {x:x-canvas.left, y:y-canvas.top, move,
        x1:x1 === undefined ? undefined : x1-canvas.left, y1:y1 === undefined ? undefined : y1-canvas.top,
        x2:x2 === undefined ? undefined : x2-canvas.left, y2:y2 === undefined ? undefined : y2-canvas.top};
    });
    return {...base('shape',box), shape:'freeform', path, fill, stroke, strokeWidth};
  }
  const svgNonvisual = new Set(['defs','title','desc','metadata']);
  function svgPaint(element, opacity, clip, output) {
    const style = getComputedStyle(element), tag = element.localName;
    if (svgNonvisual.has(tag) || style.display === 'none' || style.visibility === 'hidden') return;
    const alpha = opacity * Number(style.opacity);
    if (!alpha) return;
    checkEffects(element, style);
    if (tag === 'svg' || tag === 'g') {
      const children = [];
      const childClip = tag === 'svg' && style.overflow !== 'visible' ?
        intersection(clip, element.getBoundingClientRect()) : clip;
      for (const child of element.children) svgPaint(child, alpha, childClip, children);
      checkOpacity(element, Number(style.opacity), children);
      if (tag === 'g' && children.length > 1) output.push(groupFor(children, label(element)));
      else output.push(...children);
      return;
    }
    if (!['rect','ellipse','circle','line','polygon','polyline','path','text'].includes(tag)) unsupported(element, `an inline SVG ${tag}`);
    const matrix = element.getScreenCTM();
    if (!matrix) unsupported(element, 'an unavailable SVG transform');
    const mapped = (x,y) => new DOMPoint(x,y).matrixTransform(matrix);
    const sx = Math.hypot(matrix.a, matrix.b), sy = Math.hypot(matrix.c,matrix.d);
    const strokeWidth = px(style.strokeWidth) * (style.vectorEffect === 'non-scaling-stroke' ? 1 : sx);
    const stroke = color(style.stroke, alpha * Number(style.strokeOpacity));
    const fill = color(style.fill, alpha * Number(style.fillOpacity));
    if ((normal(style.strokeDasharray) && stroke !== 'transparent') || style.stroke.includes('url(') ||
        style.fill.includes('url(') || normal(style.markerStart) || normal(style.markerEnd) || normal(style.markerMid)) {
      unsupported(element, 'SVG dash, marker or paint-server styling');
    }
    const length = name => element[name].baseVal.value;
    const svgLine = (one, two) => {
      if (stroke === 'transparent' || !strokeWidth) return;
      if ([one,two].some(point => !contained(asRect(point.x,point.y,0,0), clip))) unsupported(element, 'a clipped SVG line');
      output.push(line(one.x,one.y,two.x,two.y,stroke,strokeWidth));
    };
    if (tag === 'line') svgLine(mapped(length('x1'),length('y1')), mapped(length('x2'),length('y2')));
    else if (tag === 'text') {
      const text = element.textContent.trim();
      if (!text || fill === 'transparent') return;
      const box = element.getBoundingClientRect();
      if (element.children.length || stroke !== 'transparent' || !same(matrix.b,0) || !same(matrix.c,0) ||
          !same(sx,sy) || !contained(box,clip) || element.rotate.baseVal.numberOfItems) {
        unsupported(element, 'complex SVG text');
      }
      const run = textStyle(element,style,alpha,sx,style.textDecorationLine);
      output.push({...base('text',box), paragraphs:[{runs:[{...run, text, color:fill}], align:'left'}]});
    } else if (tag === 'path') {
      const box = element.getBoundingClientRect();
      if (!contained(box,clip)) unsupported(element, 'a clipped SVG path');
      const commands = pathCommands(element,mapped);
      if (commands.length) output.push(freeform(box,commands,fill,stroke,strokeWidth));
    } else if (tag === 'polygon' || tag === 'polyline') {
      const points = Array.from(element.points, point => mapped(point.x, point.y));
      if (points.length < 2) return;
      const left = Math.min(...points.map(point => point.x)), top = Math.min(...points.map(point => point.y));
      const right = Math.max(...points.map(point => point.x)), bottom = Math.max(...points.map(point => point.y));
      const box = asRect(left,top,right-left,bottom-top);
      if (!contained(box, clip)) unsupported(element, 'a clipped SVG polygon');
      if (fill !== 'transparent' && tag === 'polygon') {
        if (points.length === 3 && points.some(point => same(point.x, (left+right)/2) && same(point.y, top)) &&
            points.filter(point => same(point.y, bottom)).length === 2) {
          output.push({...base('shape',box), shape:'triangle', fill, stroke, strokeWidth});
          return;
        } else if (points.length === 4 && points.every(point =>
            same(point.x,(left+right)/2) !== same(point.y,(top+bottom)/2))) {
          output.push({...base('shape',box), shape:'diamond', fill, stroke, strokeWidth});
          return;
        }
      }
      const commands = points.map((point,index) => ({x:point.x,y:point.y,move:index === 0}));
      if (tag === 'polygon') commands.push({close:true});
      output.push(freeform(box,commands,fill,stroke,strokeWidth));
    } else {
      if (!same(matrix.a*matrix.c + matrix.b*matrix.d,0)) unsupported(element, 'a skewed SVG shape');
      const bounds = element.getBBox(), center = mapped(bounds.x+bounds.width/2,bounds.y+bounds.height/2);
      const width = bounds.width*sx, height = bounds.height*sy;
      if (!width || !height) return;
      if (!contained(element.getBoundingClientRect(),clip)) unsupported(element, 'a clipped SVG shape');
      const radius = tag === 'rect' ? Math.max(px(style.rx), px(style.ry)) * sx : 0;
      const shape = tag === 'rect' ? radius ? 'roundRect' : 'rect' : 'ellipse';
      output.push({...base('shape',asRect(center.x-width/2,center.y-height/2,width,height)), shape, radius,
        fill, stroke, strokeWidth, rotation:Math.atan2(matrix.b,matrix.a)*180/Math.PI});
    }
  }
  const inlineTextTags = new Set(['a','abbr','b','bdi','bdo','cite','code','del','dfn','em','i','ins','kbd',
    'mark','q','s','samp','small','span','strong','sub','sup','time','u','var','wbr']);
  const hiddenTags = new Set(['style','script','template','noscript','link','meta','title']);
  const replacedTags = new Set(['canvas','video','audio','iframe','object','embed','input','select','textarea','meter','progress']);
  const initialState = {angle:0, scale:1};
  const backgroundStage = document.getElementById('capture-stage');
  function paintBounds(item) {
    const margin = (item.strokeWidth || 0) / 2;
    if (item.type === 'line') {
      return asRect(Math.min(item.x,item.x2)-margin, Math.min(item.y,item.y2)-margin,
        item.width+2*margin, item.height+2*margin);
    }
    const angle = (item.rotation || 0) * Math.PI / 180;
    const width = Math.abs(item.width*Math.cos(angle)) + Math.abs(item.height*Math.sin(angle));
    const height = Math.abs(item.width*Math.sin(angle)) + Math.abs(item.height*Math.cos(angle));
    return asRect(item.x+(item.width-width)/2-margin, item.y+(item.height-height)/2-margin,
      width+2*margin, height+2*margin);
  }
  function groupFor(elements, name) {
    const bounds = elements.map(paintBounds);
    const x = Math.min(...bounds.map(item => item.left)), y = Math.min(...bounds.map(item => item.top));
    const width = Math.max(...bounds.map(item => item.right)) - x;
    const height = Math.max(...bounds.map(item => item.bottom)) - y;
    return {type:'group', x,y,width,height, elements, name};
  }
  function checkOpacity(element, opacity, items) {
    if (opacity >= 1) return;
    const boxes = [];
    function visit(item) {
      if (item.type === 'group') {
        item.elements.forEach(visit);
        return;
      }
      if (item.type === 'shape' && item.strokeWidth && item.stroke !== 'transparent' &&
          (item.gradient || item.fill !== 'transparent')) {
        unsupported(element, 'group opacity across a fill and stroke');
      }
      const bounds = paintBounds(item);
      if (boxes.some(box => {
        const overlap = intersection(box,bounds);
        return overlap.width > 0.25 && overlap.height > 0.25;
      })) unsupported(element, 'group opacity across overlapping objects');
      boxes.push(bounds);
    }
    items.forEach(visit);
  }
  function clipping(element, rect, style, state, current) {
    const horizontal = ['hidden','clip','scroll','auto'].includes(style.overflowX);
    const vertical = ['hidden','clip','scroll','auto'].includes(style.overflowY);
    if ((!horizontal && !vertical) || element === slide) return current;
    if (!same(state.angle,0) || rounded(element,rect,style,state.scale).shape !== 'rect') {
      unsupported(element, 'a rotated or rounded clipping region');
    }
    if (element.scrollLeft || element.scrollTop) unsupported(element, 'a scrolled clipping region');
    const x = rect.left + element.clientLeft * state.scale;
    const y = rect.top + element.clientTop * state.scale;
    return intersection(current, asRect(horizontal ? x : current.left, vertical ? y : current.top,
      horizontal ? element.clientWidth * state.scale : current.width,
      vertical ? element.clientHeight * state.scale : current.height));
  }
  function chartObject(element, rect, clip, opacity, state) {
    if (opacity !== 1 || state.angle || !contained(rect, clip)) unsupported(element, 'a rotated, translucent, or clipped chart');
    let chart;
    try {
      chart = JSON.parse(element.getAttribute('data-pptx-export-chart'));
    } catch {
      unsupported(element, 'malformed native chart data');
    }
    const {categories, series, minimum, maximum, majorUnit, fontFamily, fontSize, color, axisColor, gridColor,
      gapWidth, legend, title, showValues, chartType} = chart || {};
    if (!Array.isArray(categories) || !categories.length || categories.some(item => typeof item !== 'string') ||
        !Array.isArray(series) || !series.length || series.some(item => typeof item?.name !== 'string' ||
          !Array.isArray(item.values) || item.values.length !== categories.length || item.values.some(value => !Number.isFinite(value)))) {
      unsupported(element, 'invalid native chart categories or series');
    }
    return {...base('chart', rect), categories, series, minimum, maximum, majorUnit, fontFamily, fontSize, color, axisColor,
      gridColor, gapWidth, legend, title, showValues, chartType, name:label(element)};
  }
  function marker(element, style, lines, opacity, state, paint) {
    if (!lines.length || style.listStyleType === 'none') return;
    if (style.listStyleImage !== 'none') unsupported(element, 'a custom list marker image');
    const list = element.parentElement;
    const type = style.listStyleType;
    let text = {disc:'•', circle:'◦', square:'▪'}[type];
    if (type === 'decimal' || type === 'decimal-leading-zero') {
      const items = Array.from(list.children).filter(item => item.localName === 'li');
      const direction = list.reversed ? -1 : 1;
      const first = list.hasAttribute('start') ? list.start : list.reversed ? items.length : 1;
      let value = first;
      for (const item of items) {
        if (item.hasAttribute('value')) value = item.value;
        if (item === element) break;
        value += direction;
      }
      text = `${type === 'decimal-leading-zero' && value >= 0 ? String(value).padStart(2, '0') : value}.`;
    }
    if (!text) unsupported(element, 'a complex list marker');
    if (style.listStylePosition === 'inside') unsupported(element, 'an inside list marker');
    const [line] = lines;
    const run = textStyle(element,style,opacity,state.scale,style.textDecorationLine);
    const markerWidth = run.fontSize * Math.max(0.85, text.length * 0.55);
    const x = line.left - markerWidth - run.fontSize * 0.25;
    paint({...base('text',asRect(x,line.y,markerWidth,line.height)), paragraphs:[{runs:[{...run,text}], align:'right'}]});
  }
  function createsLayer(element, style) {
    return (style.position !== 'static' && style.zIndex !== 'auto') ||
      (style.zIndex !== 'auto' && /flex|grid/.test(getComputedStyle(element.parentElement).display)) ||
      normal(style.transform) || normal(style.translate) || Number(style.opacity) !== 1 ||
      style.isolation === 'isolate' || ['fixed','sticky'].includes(style.position);
  }
  const queue = () => ({back:[], ordinary:[], positioned:[], front:[]});
  const layer = (item, z, local) => {
    if (z < 0) local.back.push({z,item});
    else if (z > 0) local.front.push({z,item});
    else local.positioned.push(item);
  };
  const finish = local => [
    ...local.back.sort((a,b) => a.z-b.z).flatMap(entry => entry.item),
    ...local.ordinary.flat(),
    ...local.positioned.flat(),
    ...local.front.sort((a,b) => a.z-b.z).flatMap(entry => entry.item),
  ];
  let visited = 0;
  async function walk(element, opacity, priorState, clip, scope, inline = null, decorations = '', positioned = false) {
    if (++visited > 6000) unsupported(slide, 'more than 6,000 HTML elements');
    const tag = element.localName;
    if (hiddenTags.has(tag)) return;
    const style = getComputedStyle(element);
    if (style.display === 'none' || style.contentVisibility === 'hidden') return;
    opacity *= Number(style.opacity);
    if (opacity <= 0) return;
    const visible = style.visibility === 'visible';
    const state = transform(element, style, priorState);
    const rect = element.getBoundingClientRect();
    if (element !== slide && rect.width && rect.height && invisible(rect, clip) &&
        ['hidden','clip','scroll','auto'].includes(style.overflowX)) return;
    if (visible) checkEffects(element, style);
    const group = element.hasAttribute('data-pptx-export-group') || element.hasAttribute('data-group') ||
      Boolean(element.hasAttribute('data-element-id') && element.querySelector('[data-element-id]'));
    const separate = element === slide || group || createsLayer(element, style);
    const local = separate ? queue() : scope;
    positioned = !separate && (positioned || style.position !== 'static');
    const normalPaint = positioned ? local.positioned : local.ordinary;
    const background = [];
    const standard = [];
    const paintBackground = object => background.push(object);
    const paintStandard = object => standard.push(object);
    const addStandard = () => { if (standard.length) normalPaint.push(standard.splice(0)); };
    decorations += ' ' + style.textDecorationLine;
    if (visible && replacedTags.has(tag)) unsupported(element, `an HTML ${tag} element`);
    if (visible && rect.width && rect.height && !invisible(rect, clip) && style.display !== 'contents') {
      if (element !== slide) await paintBox(element, rect, style, opacity, state, clip, paintBackground);
      if (element.hasAttribute('data-pptx-export-chart')) paintStandard(chartObject(element, rect, clip, opacity, state));
      else if (tag === 'img') paintStandard(imageObject(element, rect, style, opacity, state, clip));
      else if (tag === 'svg') svgPaint(element, opacity, clip, standard);
      else if (tag === 'table') paintStandard(tableObject(element, rect, style, opacity, state, clip));
    }
    if (!separate && background.length) normalPaint.push(background);
    if (!['img','svg','table'].includes(tag) && !element.hasAttribute('data-pptx-export-chart')) {
      const childClip = clipping(element, rect, style, state, clip);
      const ownsLines = inline === null;
      const lines = inline || [];
      const flush = () => {
        if (ownsLines && lines.length) {
          if (style.display === 'list-item') marker(element, style, lines, opacity, state, paintStandard);
          textObjects(element, lines.splice(0), style, paintStandard);
          addStandard();
        }
      };
      const children = Array.from(element.childNodes);
      if (/flex|grid/.test(style.display)) {
        const order = child => child instanceof Element ? Number(getComputedStyle(child).order) : 0;
        children.sort((a,b) => order(a) - order(b));
      }
      for (const child of children) {
        if (child.nodeType === Node.TEXT_NODE) addText(child, style, opacity, state, decorations, lines, childClip);
        if (!(child instanceof Element) || child.localName === 'br') continue;
        const childStyle = getComputedStyle(child);
        const joinsLine = (inlineTextTags.has(child.localName) && childStyle.display === 'inline' &&
          !createsLayer(child, childStyle)) || childStyle.display === 'contents';
        if (!joinsLine) flush();
        await walk(child, opacity, state, childClip, local, joinsLine ? lines : null, decorations, positioned);
      }
      flush();
    }
    addStandard();
    if (separate) {
      const entries = [...background, ...finish(local)];
      checkOpacity(element, Number(style.opacity), entries);
      if (element === slide) return entries;
      const objects = group && entries.length > 1 ? [groupFor(entries, label(element))] : entries;
      if (objects.length) {
        if (group && !createsLayer(element, style) && style.position === 'static') scope.ordinary.push(objects);
        else layer(objects, Number(style.zIndex) || 0, scope);
      }
    }
  }
  let imageBytes = 0, nativeObjects = 0;
  async function prepareObjects(elements, cache) {
    for (const element of elements) {
      nativeObjects++;
      if (element.type === 'table') nativeObjects += element.rows.reduce((total,row) => total+row.length, 0);
      if (element.type === 'chart') nativeObjects += element.series.length * element.categories.length;
      if (nativeObjects > maxObjects) throw new Error('The presentation exceeds the editable PowerPoint object budget. Export fewer objects at a time.');
      if (element.type === 'group') await prepareObjects(element.elements, cache);
      if (element.type !== 'image') continue;
      imageBytes += element.data.length;
      checkBytes(imageBytes);
      if (!/^data:image\/svg\+xml[;,]/i.test(element.data)) continue;
      if (!cache.has(element.data)) {
        const image = new Image();
        image.src = element.data;
        await image.decode();
        const preview = document.createElement('canvas');
        const scale = Math.min(1, 2048 / Math.max(image.naturalWidth, image.naturalHeight));
        preview.width = Math.max(1, Math.round(image.naturalWidth * scale));
        preview.height = Math.max(1, Math.round(image.naturalHeight * scale));
        preview.getContext('2d').drawImage(image, 0, 0, preview.width, preview.height);
        cache.set(element.data, preview.toDataURL('image/png'));
      }
      element.fallbackData = cache.get(element.data);
      imageBytes += element.fallbackData.length;
      checkBytes(imageBytes);
    }
  }
  function checkBytes(size) {
    if (size > maxBytes) throw new Error('The presentation exceeds the editable PowerPoint data budget. Reduce picture sizes or export fewer slides at a time.');
  }
  return (async () => {
    try {
      checkMotion();
      const rootStyle = getComputedStyle(slide);
      if (rootStyle.transform !== 'none' || Number(rootStyle.opacity) !== 1) unsupported(slide, 'a transformed or translucent slide canvas');
      const elements = [];
      let fill = 'rgb(255,255,255)';
      let gradient;
      for (const container of [document.documentElement, document.body, backgroundStage, slide]) {
        const computed = getComputedStyle(container);
        const bg = background(container, computed, 1);
        const borderSides = border(computed, 1, 1, container);
        if (bg.source || borderSides.some(side => side.strokeWidth)) {
          checkEffects(container, computed);
          await paintBox(container, container.getBoundingClientRect(), computed, 1, initialState, canvas, item => elements.push(item));
        } else if (normal(computed.backgroundImage) && !bg.gradient) unsupported(container, 'a complex slide background');
        else if (bg.gradient || bg.fill !== 'transparent') {
          if (elements.length || bg.fill.startsWith('rgba(')) {
            elements.push({...base('shape',canvas), fill:bg.fill, gradient:bg.gradient});
          } else {
            fill = bg.fill === 'transparent' ? fill : bg.fill;
            gradient = bg.gradient;
          }
        }
      }
      elements.push(...await walk(slide, 1, initialState, canvas, queue()));
      await prepareObjects(elements, new Map());
      const layout = {width:canvas.width, height:canvas.height, background:fill, gradient, elements};
      const modelBytes = new Blob([JSON.stringify(layout)]).size;
      checkBytes(modelBytes);
      return {layout,modelBytes,nativeObjects};
    } catch (error) {
      return {error:error.message};
    }
  })();
}
