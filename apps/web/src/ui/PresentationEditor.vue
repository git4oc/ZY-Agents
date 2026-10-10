<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, nextTick, computed } from 'vue'
import * as fabric from 'fabric'
import { renderPresentationPptx, saveBlueprint } from '../api/documents'
import { t, useLocale } from '../hooks/i18n'
import { authenticatedJsonHeaders } from '../api/authHeaders'
import { canonicalPresentationStyle } from '../lib/presentationStyle'
import { resolveAlignedTextTop, resolveFontWeight, resolveTextInsets } from '../lib/presentationTextLayout'
import { linePageEndpoints } from '../lib/presentationLineGeometry'

// ---------------------------------------------------------------------------
// Props 与事件
// ---------------------------------------------------------------------------
const props = defineProps<{
  blueprint: any
  userId: string
  blueprintObjectPath: string
}>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'exported', result: any): void
  (e: 'saved', blueprint: any): void
}>()

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------
const CANVAS_W = 1600
const CANVAS_H = 900

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------
const canvasEl = ref<HTMLCanvasElement | null>(null)
const canvasWrapperEl = ref<HTMLDivElement | null>(null)
const displayScale = ref(0.65)
let resizeOb: ResizeObserver | null = null
let fc: fabric.Canvas | null = null

const currentPageIndex = ref(0)
const pages = ref<any[]>([])
const exporting = ref(false)
const saving = ref(false)

// 工具栏状态
const selectedObj = ref<fabric.FabricObject | null>(null)
const selectedIsText = ref(false)
const selectedIsShape = ref(false)
// 文本工具
const toolFontSize = ref(18)
const toolColor = ref('#222222')
const toolBold = ref(false)
const toolAlign = ref('left')
// 形状工具
const toolFillMode = ref<'solid' | 'gradient'>('solid')
const toolFillColor = ref('#ffffff')
const toolGradientFrom = ref('#3b82f6')
const toolGradientTo = ref('#8b5cf6')
const toolGradientAngle = ref(135)
const toolStrokeColor = ref('#000000')
const toolOpacity = ref(100)
const toolCornerRadius = ref(0)
const { locale } = useLocale()

const totalPages = computed(() => pages.value.length)

// ---------------------------------------------------------------------------
// 渐变与颜色工具
// ---------------------------------------------------------------------------
function isTransparent(val: string | undefined): boolean {
  if (!val) return true
  const v = val.trim().toLowerCase()
  return v === 'transparent' || v === 'none' || v === 'inherit' || v === 'initial' || v === ''
}

interface GradientStop { offset: number; color: string }

function parseLinearGradient(val: string): { angle: number; stops: GradientStop[] } | null {
  const m = val.match(/linear-gradient\s*\((.+)\)\s*$/i)
  if (!m) return null
  const inner = m[1]
  // 按顶层逗号切分
  const parts: string[] = []
  let depth = 0, buf = ''
  for (const ch of inner) {
    if (ch === '(') depth++
    else if (ch === ')') depth--
    else if (ch === ',' && depth === 0) { parts.push(buf.trim()); buf = ''; continue }
    buf += ch
  }
  if (buf.trim()) parts.push(buf.trim())
  if (parts.length < 2) return null

  let angle = 180
  let colorParts = parts
  const first = parts[0].trim().toLowerCase()
  const angleMatch = first.match(/^(-?\d+(?:\.\d+)?)\s*deg$/)
  if (angleMatch) {
    angle = parseFloat(angleMatch[1])
    colorParts = parts.slice(1)
  } else if (first.startsWith('to ')) {
    const dirMap: Record<string, number> = {
      'to top': 0, 'to right': 90, 'to bottom': 180, 'to left': 270,
      'to top right': 45, 'to bottom right': 135, 'to bottom left': 225, 'to top left': 315,
    }
    angle = dirMap[first] ?? 180
    colorParts = parts.slice(1)
  }

  const stops: GradientStop[] = []
  for (let i = 0; i < colorParts.length; i++) {
    const p = colorParts[i].trim()
    const posMatch = p.match(/(\d+(?:\.\d+)?)\s*%\s*$/)
    let offset = i / Math.max(1, colorParts.length - 1)
    let colorStr = p
    if (posMatch) {
      offset = parseFloat(posMatch[1]) / 100
      colorStr = p.slice(0, posMatch.index).trim()
    }
    stops.push({ offset, color: colorStr })
  }
  return stops.length >= 2 ? { angle, stops } : null
}

function angleToCoordsPercent(angle: number): { x1: number; y1: number; x2: number; y2: number } {
  // Fabric 渐变坐标为对象内的 0–1 归一值
  const rad = ((angle - 90) * Math.PI) / 180
  const cos = Math.cos(rad), sin = Math.sin(rad)
  return {
    x1: 0.5 - cos * 0.5,
    y1: 0.5 - sin * 0.5,
    x2: 0.5 + cos * 0.5,
    y2: 0.5 + sin * 0.5,
  }
}

function makeFabricGradient(val: string, width: number, height: number): fabric.Gradient<'linear'> | string {
  if (isTransparent(val)) return 'transparent'
  const parsed = parseLinearGradient(val)
  if (!parsed) return val // plain color string
  const coords = angleToCoordsPercent(parsed.angle)
  return new fabric.Gradient({
    type: 'linear',
    gradientUnits: 'percentage',
    coords: { x1: coords.x1, y1: coords.y1, x2: coords.x2, y2: coords.y2 },
    colorStops: parsed.stops.map(s => ({ offset: s.offset, color: s.color })),
  })
}

function resolveBackground(val: string | undefined, width: number, height: number): string | fabric.Gradient<'linear'> {
  if (!val || isTransparent(val)) return 'transparent'
  return makeFabricGradient(val, width, height)
}

// ---------------------------------------------------------------------------
// Blueprint → Fabric 转换
// ---------------------------------------------------------------------------
function loadPageToCanvas(pageIndex: number) {
  if (!fc) return
  guideLines.length = 0
  fc.clear()
  // 重置背景：fc.clear() 不会重置 backgroundColor
  fc.backgroundColor = '#ffffff'
  const page = pages.value[pageIndex]
  if (!page) return

  // 页面背景（可能是渐变）
  const pageBg = page.style?.background || page.style?.background_color || props.blueprint?.theme?.page_background || '#ffffff'
  const bgParsed = parseLinearGradient(pageBg)
  if (bgParsed) {
    // canvas.backgroundColor 不支持渐变，改为铺一个整页矩形
    const bgRect = new fabric.Rect({
      left: 0, top: 0, width: CANVAS_W, height: CANVAS_H,
      fill: makeFabricGradient(pageBg, CANVAS_W, CANVAS_H),
      selectable: false, evented: false,
    })
    fc.add(bgRect)
  } else {
    fc.backgroundColor = isTransparent(pageBg) ? '#ffffff' : pageBg
  }

  const blocks = [...(page.blocks || [])].sort((a: any, b: any) => (a.z_index || 0) - (b.z_index || 0))
  for (const block of blocks) {
    addBlockToCanvas(block, { x: 0, y: 0, w: 1, h: 1 })
  }
  fc.renderAll()
}

function resolveBlockGeometry(
  block: any,
  parentRect: { x: number; y: number; w: number; h: number },
) {
  const coordSpace = (block.coordinate_space || 'page').toLowerCase()
  let bx: number, by: number, bw: number, bh: number
  if (coordSpace === 'parent') {
    bx = parentRect.x + (block.x || 0) * parentRect.w
    by = parentRect.y + (block.y || 0) * parentRect.h
    bw = (block.w || 0) * parentRect.w
    bh = (block.h || 0) * parentRect.h
  } else {
    bx = block.x || 0; by = block.y || 0
    bw = block.w || 0; bh = block.h || 0
  }
  return {
    bx, by, bw, bh,
    left: bx * CANVAS_W,
    top: by * CANVAS_H,
    width: Math.max(1, bw * CANVAS_W),
    height: Math.max(1, bh * CANVAS_H),
  }
}

function resolveStroke(style: any): { stroke: string; strokeWidth: number } {
  const raw = style.border_color || style.border || ''
  if (!raw || isTransparent(raw)) return { stroke: 'transparent', strokeWidth: 0 }
  // 解析 "Npx solid #color"
  const m = raw.match?.(/(\d+(?:\.\d+)?)\s*px\s+solid\s+(#[0-9a-fA-F]{3,8})/)
  if (m) return { stroke: m[2], strokeWidth: parseFloat(m[1]) }
  return { stroke: raw, strokeWidth: parseFloat(style.border_width) || 1 }
}

function addIconFallback(
  canvas: fabric.Canvas, blockId: string, blockType: string,
  left: number, top: number, width: number, height: number,
  iconName: string, color: string,
) {
  const label = new fabric.Text(iconName, {
    left: left + 2, top: top + height * 0.2,
    fontSize: Math.max(8, Math.min(14, Math.min(width, height) * 0.3)),
    fill: color, selectable: true,
  })
  ;(label as any)._blockId = blockId
  ;(label as any)._blockType = blockType
  canvas.add(label)
  canvas.renderAll()
}

function addChartPlaceholder(
  canvas: fabric.Canvas, blockId: string, blockType: string,
  left: number, top: number, width: number, height: number, title: string,
) {
  const rect = new fabric.Rect({
    left, top, width, height,
    fill: '#f8fafc', stroke: '#94a3b8', strokeWidth: 1,
    rx: 10, ry: 10, selectable: true,
  })
  ;(rect as any)._blockId = blockId
  ;(rect as any)._blockType = blockType
  canvas.add(rect)
  canvas.add(new fabric.Text(title, {
    left: left + 10, top: top + 10,
    fontSize: 14, fill: '#64748b',
    selectable: false, evented: false,
  }))
  canvas.renderAll()
}

function addImagePlaceholder(
  canvas: fabric.Canvas, block: any, blockType: string,
  left: number, top: number, width: number, height: number,
) {
  const promptText = block.image_prompt || t('ppt.image_placeholder')
  const rect = new fabric.Rect({
    left, top, width, height,
    fill: '#e8ecf0',
    stroke: '#b0b8c4',
    strokeWidth: 2,
    strokeDashArray: [6, 4],
    rx: 8, ry: 8,
    selectable: true,
  })
  ;(rect as any)._blockId = block.id
  ;(rect as any)._blockType = blockType
  canvas.add(rect)
  const label = new fabric.Text(promptText, {
    left: left + 10, top: top + height / 2 - 8,
    fontSize: 13,
    fill: '#8896a7',
    selectable: false,
    evented: false,
  })
  canvas.add(label)
  canvas.renderAll()
}

// 为带可见样式的块绘制背景/边框容器矩形。
// 样式需与 HTML 渲染路径保持一致：那边会把全部样式属性落到块自身的 <div> 上。
function renderBlockContainer(
  blockId: string, style: any,
  left: number, top: number, width: number, height: number,
): boolean {
  if (!fc) return false
  const bgVal = style.background || style.background_color || ''
  const hasBg = bgVal && !isTransparent(bgVal)

  // 判断是否存在边框（整体边框或 border_left 这类单边）
  const borderColor = style.border_color || ''
  const borderLeft = style.border_left || ''
  const borderRight = style.border_right || ''
  const borderTop = style.border_top || ''
  const borderBottom = style.border_bottom || ''
  const hasBorder = (borderColor && !isTransparent(borderColor))
    || borderLeft || borderRight || borderTop || borderBottom
  const hasBoxShadow = style.box_shadow && style.box_shadow !== 'none'

  if (!hasBg && !hasBorder && !hasBoxShadow) return false

  const { stroke, strokeWidth } = resolveStroke(style)
  const opacity = style.opacity != null ? parseFloat(style.opacity) : 1.0

  // 把 box_shadow 解析为 Fabric Shadow
  let shadow: fabric.Shadow | undefined
  if (hasBoxShadow) {
    const sv = style.box_shadow as string
    // 预设名
    const presets: Record<string, string> = {
      soft: '0 8px 24px rgba(15,23,42,0.10)',
      subtle: '0 4px 14px rgba(15,23,42,0.08)',
      medium: '0 10px 28px rgba(15,23,42,0.14)',
      strong: '0 16px 40px rgba(15,23,42,0.18)',
    }
    const resolved = presets[sv.toLowerCase()] || sv
    // 解析 "offsetX offsetY blur color"
    const sm = resolved.match(/(-?\d+)(?:px)?\s+(-?\d+)(?:px)?\s+(\d+)(?:px)?\s+(rgba?\([^)]+\)|#[0-9a-fA-F]{3,8})/)
    if (sm) {
      shadow = new fabric.Shadow({
        offsetX: parseInt(sm[1]),
        offsetY: parseInt(sm[2]),
        blur: parseInt(sm[3]),
        color: sm[4],
      })
    }
  }

  const rect = new fabric.Rect({
    left, top, width, height,
    fill: hasBg ? resolveBackground(bgVal, width, height) : '#ffffff',
    stroke, strokeWidth,
    rx: parseFloat(style.border_radius) || 0,
    ry: parseFloat(style.border_radius) || 0,
    opacity: isNaN(opacity) ? 1 : opacity,
    shadow: shadow || null,
    selectable: false, evented: false,
  })
  ;(rect as any)._blockId = blockId + '_container'
  ;(rect as any)._blockType = 'container'

  fc.add(rect)
  drawSideBorders(style, left, top, width, height)
  return true
}

// 单边边框：用同色矩形绘制，并保持与整体一致的圆角
function drawSideBorders(style: any, left: number, top: number, width: number, height: number) {
  if (!fc) return
  const radius = parseFloat(style.border_radius) || 0
  const borderDefs = [
    { key: 'border_left',   getRect: (bw: number) => ({ x: left, y: top, w: bw, h: height, rx: radius, ry: radius }) },
    { key: 'border_right',  getRect: (bw: number) => ({ x: left + width - bw, y: top, w: bw, h: height, rx: radius, ry: radius }) },
    { key: 'border_top',    getRect: (bw: number) => ({ x: left, y: top, w: width, h: bw, rx: radius, ry: radius }) },
    { key: 'border_bottom', getRect: (bw: number) => ({ x: left, y: top + height - bw, w: width, h: bw, rx: radius, ry: radius }) },
  ]
  for (const def of borderDefs) {
    const val = style[def.key]
    if (!val) continue
    const m = (val as string).match?.(/(\d+(?:\.\d+)?)\s*px\s+solid\s+(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\))/)
    if (!m) continue
    const bw = parseFloat(m[1])
    const r = def.getRect(bw)
    const br = new fabric.Rect({
      left: r.x, top: r.y, width: r.w, height: r.h,
      fill: m[2],
      rx: r.rx, ry: r.ry,
      selectable: false, evented: false,
    })
    ;(br as any)._blockType = 'container'
    ;(br as any)._isGuide = true
    fc.add(br)
  }
}

function addBlockToCanvas(
  block: any,
  parentRect: { x: number; y: number; w: number; h: number },
) {
  if (!fc) return
  const type = (block.type || 'text_box').toLowerCase()
  const { bx, by, bw, bh, left, top, width, height } = resolveBlockGeometry(block, parentRect)
  const style = canonicalPresentationStyle(block.style, type)
  const bgVal = style.background || style.background_color || ''
  const { stroke, strokeWidth } = resolveStroke(style)
  const opacity = Number.isFinite(parseFloat(style.opacity)) ? parseFloat(style.opacity) : 1

  const addTextObject = (
    content: string,
    boxLeft: number,
    boxTop: number,
    boxWidth: number,
    boxHeight: number,
    textStyle: any,
    attachBlockIdentity = true,
  ) => {
    if (!fc || !content) return
    const insets = resolveTextInsets(textStyle.padding)
    const textWidth = Math.max(1, boxWidth - insets.left - insets.right)
    const fontSize = parseFloat(textStyle.font_size) || 18
    const lineHeight = parseFloat(textStyle.line_height) || 1.2
    const tb = new fabric.Textbox(content.replace(/\\n/g, '\n'), {
      left: boxLeft + insets.left,
      top: boxTop + insets.top,
      width: textWidth,
      fontSize,
      fontWeight: resolveFontWeight(textStyle.font_weight),
      fontStyle: textStyle.font_style === 'italic' ? 'italic' : 'normal',
      fill: textStyle.color || '#222222',
      textAlign: (textStyle.text_align || 'left') as any,
      fontFamily: textStyle.font_family || "'PingFang SC', 'Microsoft YaHei', sans-serif",
      lineHeight,
      splitByGrapheme: true,
      selectable: attachBlockIdentity,
      editable: attachBlockIdentity,
      opacity,
    })
    tb.set({
      top: resolveAlignedTextTop(
        boxTop,
        boxHeight,
        Number(tb.height || fontSize * lineHeight),
        insets,
        textStyle.vertical_align,
      ),
    })
    if (attachBlockIdentity) {
      ;(tb as any)._blockId = block.id
      ;(tb as any)._blockType = type
      ;(tb as any)._layoutBox = {
        left: boxLeft,
        top: boxTop,
        width: boxWidth,
        height: boxHeight,
        textLeft: Number(tb.left || 0),
        textTop: Number(tb.top || 0),
        textWidth: Number(tb.width || textWidth),
      }
    } else {
      ;(tb as any)._isGuide = true
      ;(tb as any)._blockType = 'container-label'
    }
    fc.add(tb)
  }

  if (type === 'rectangle' || type === 'group') {
    // 解析该形状的 box-shadow
    let shapeShadow: fabric.Shadow | undefined
    const bsVal = style.box_shadow
    if (bsVal && bsVal !== 'none') {
      const presets: Record<string, string> = {
        soft: '0 8px 24px rgba(15,23,42,0.10)', subtle: '0 4px 14px rgba(15,23,42,0.08)',
        medium: '0 10px 28px rgba(15,23,42,0.14)', strong: '0 16px 40px rgba(15,23,42,0.18)',
      }
      const resolved = presets[(bsVal + '').toLowerCase()] || bsVal
      const sm = (resolved + '').match(/(-?\d+)(?:px)?\s+(-?\d+)(?:px)?\s+(\d+)(?:px)?\s+(rgba?\([^)]+\)|#[0-9a-fA-F]{3,8})/)
      if (sm) shapeShadow = new fabric.Shadow({ offsetX: +sm[1], offsetY: +sm[2], blur: +sm[3], color: sm[4] })
    }
    const rect = new fabric.Rect({
      left, top, width, height,
      fill: resolveBackground(bgVal, width, height),
      stroke, strokeWidth,
      rx: parseFloat(style.border_radius) || 0,
      ry: parseFloat(style.border_radius) || 0,
      shadow: shapeShadow || null,
      opacity,
      selectable: true,
    })
    ;(rect as any)._blockId = block.id
    ;(rect as any)._blockType = type
    fc.add(rect)
    drawSideBorders(style, left, top, width, height)

    const childRect = { x: bx, y: by, w: bw, h: bh }
    for (const child of (block.children || [])) {
      addBlockToCanvas(child, childRect)
    }

  } else if (type === 'circle') {
    const r = Math.min(width, height) / 2
    const circle = new fabric.Circle({
      left, top, radius: r,
      fill: resolveBackground(bgVal, r * 2, r * 2),
      stroke, strokeWidth,
      opacity,
      selectable: true,
    })
    ;(circle as any)._blockId = block.id
    ;(circle as any)._blockType = type
    fc.add(circle)
    addTextObject(block.content || '', left, top, r * 2, r * 2, {
      ...style,
      text_align: style.text_align || 'center',
      vertical_align: style.vertical_align || 'middle',
    }, false)

  } else if (type === 'text_box') {
    // 容器（背景、边框、阴影）需与 HTML 渲染路径一致
    renderBlockContainer(block.id, style, left, top, width, height)

    addTextObject(block.content || '', left, top, width, height, style)

  } else if (type === 'line') {
    const coordSpace = (block.coordinate_space || 'page').toLowerCase()
    const endpointX = block.x2 ?? block.x ?? 0
    const endpointY = block.y2 ?? block.y ?? 0
    const x1 = bx * CANVAS_W
    const y1 = by * CANVAS_H
    const x2 = (coordSpace === 'parent' ? parentRect.x + endpointX * parentRect.w : endpointX) * CANVAS_W
    const y2 = (coordSpace === 'parent' ? parentRect.y + endpointY * parentRect.h : endpointY) * CANVAS_H
    const lineColor = style.color || style.background || '#000000'
    const lineWeight = parseFloat(style.line_weight) || 2

    const line = new fabric.Line([x1, y1, x2, y2], {
      stroke: isTransparent(lineColor) ? '#000' : lineColor,
      strokeWidth: lineWeight,
      opacity,
      selectable: true,
    })
    ;(line as any)._blockId = block.id
    ;(line as any)._blockType = type
    fc.add(line)

  } else if (type === 'icon') {
    renderBlockContainer(block.id, style, left, top, width, height)

    let iconName = (block.icon || block.content || 'sparkles').trim()
    iconName = iconName.replace(/^(tabler:|icon[-:])/i, '').trim() || 'sparkles'
    const iconColor = style.color || props.blueprint?.theme?.accent_color || '#3b82f6'
    const colorHex = iconColor.replace('#', '')
    const canvas = fc
    // 图标需与 HTML 渲染路径一致：SVG 落在块的 (left,top,width,height) 内，
    // 以 width="100%" height="100%" + preserveAspectRatio="xMidYMid meet" 呈现。
    // 这里先按 (width×height) 像素（DPI ×2）离屏绘制并居中，等比缩放，
    // 再把 FabricImage 放到块的 (left, top) 并缩放回块的像素尺寸。
    const ocW = Math.max(Math.round(width), 8) * 2
    const ocH = Math.max(Math.round(height), 8) * 2
    const svgUrl = `/api/icons/${encodeURIComponent(iconName)}.svg?color=${colorHex}`
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      if (!canvas) return
      const oc = document.createElement('canvas')
      oc.width = ocW
      oc.height = ocH
      const ctx = oc.getContext('2d')!
      ctx.clearRect(0, 0, ocW, ocH)
      // 等价于 preserveAspectRatio="xMidYMid meet"：
      // 按 ocW×ocH 等比缩放并居中
      const iconSize = Math.min(ocW, ocH)
      const dx = (ocW - iconSize) / 2
      const dy = (ocH - iconSize) / 2
      ctx.drawImage(img, dx, dy, iconSize, iconSize)
      // 放到块的精确位置，缩放回块的像素尺寸
      const fabricImg = new fabric.FabricImage(oc, {
        left,
        top,
        scaleX: width / ocW,
        scaleY: height / ocH,
        selectable: true,
        objectCaching: false,
      })
      ;(fabricImg as any)._blockId = block.id
      ;(fabricImg as any)._blockType = type
      canvas.add(fabricImg)
      canvas.renderAll()
    }
    img.onerror = () => addIconFallback(canvas!, block.id, type, left, top, width, height, iconName, iconColor)
    img.src = svgUrl

  } else if (type === 'chart') {
    renderBlockContainer(block.id, style, left, top, width, height)
    const chartType = block.chart_type || ''
    const chartData = block.chart_data || {}
    const chartTitle = chartData.title || block.content || t('ppt.chart_placeholder')
    const canvas = fc
    const pxW = Math.round(Math.max(width, 160))
    const pxH = Math.round(Math.max(height, 120))

    // 由 blueprint 构建主题
    const theme = props.blueprint?.theme || {}
    const chartTheme = {
      accent_color: theme.accent_color || '#2563eb',
      title_color: theme.title_color || '#0f172a',
      body_color: theme.body_color || '#334155',
      muted_color: theme.muted_color || '#64748b',
      surface_background: theme.surface_background || '#ffffff',
      page_background: theme.page_background || '#ffffff',
    }

    if (chartType) {
      fetch('/api/documents/render-chart-svg', {
        method: 'POST',
        headers: authenticatedJsonHeaders(),
        body: JSON.stringify({
          chart_type: chartType,
          chart_data: chartData,
          width: pxW,
          height: pxH,
          theme: chartTheme,
        }),
      })
        .then(r => r.ok ? r.text() : Promise.reject('chart render failed'))
        .then(svgStr => {
          if (!canvas) return
          const dataUrl = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgStr)
          return fabric.FabricImage.fromURL(dataUrl).then((img: any) => {
            if (!canvas) return
            const imgW = img.width || pxW
            const imgH = img.height || pxH
            img.set({
              left, top,
              scaleX: width / imgW,
              scaleY: height / imgH,
              selectable: true,
            })
            ;(img as any)._blockId = block.id
            ;(img as any)._blockType = type
            canvas.add(img)
            canvas.renderAll()
          })
        })
        .catch(() => {
          // 占位兜底
          addChartPlaceholder(canvas!, block.id, type, left, top, width, height, chartTitle)
        })
    } else {
      addChartPlaceholder(canvas!, block.id, type, left, top, width, height, chartTitle)
    }

  } else if (type === 'image') {
    renderBlockContainer(block.id, style, left, top, width, height)
    const src = String(block.content || '').trim()
    const isRealUrl = src.startsWith('http://') || src.startsWith('https://') || src.startsWith('/')
    if (isRealUrl) {
      // 先同步插入占位图，让图像占住正确的 z 序槽位。
      // 真实图加载完成后替换同一数组下标——否则异步 onload 会把它压到
      // 画布栈顶，盖住其后添加的文本/蒙版块。
      const canvasRef = fc!
      const slot = new fabric.Rect({
        left, top, width, height,
        fill: '#e8ecf0',
        stroke: '#b0b8c4',
        strokeWidth: 1,
        strokeDashArray: [4, 4],
        rx: 8, ry: 8,
        selectable: false,
        evented: false,
      })
      ;(slot as any)._blockId = block.id + '_image_slot'
      ;(slot as any)._blockType = 'container'
      ;(slot as any)._isGuide = true
      canvasRef.add(slot)

      const nativeImg = new Image()
      const radiusPx = (() => {
        const r = parseFloat(String(style.border_radius ?? style.borderRadius ?? '')) || 12
        return Math.max(0, Math.min(r, Math.min(width, height) / 2))
      })()
      nativeImg.onload = () => {
        if (!canvasRef) return
        const iw = nativeImg.naturalWidth || nativeImg.width || 1
        const ih = nativeImg.naturalHeight || nativeImg.height || 1
        // 按设备像素比放大，避免圆边出现锯齿。
        const dpr = 2
        const ocW = Math.max(Math.round(width), 8) * dpr
        const ocH = Math.max(Math.round(height), 8) * dpr
        const oc = document.createElement('canvas')
        oc.width = ocW
        oc.height = ocH
        const ctx = oc.getContext('2d')!
        // 按圆角矩形裁剪
        const r = radiusPx * dpr
        ctx.beginPath()
        ctx.moveTo(r, 0)
        ctx.lineTo(ocW - r, 0)
        ctx.quadraticCurveTo(ocW, 0, ocW, r)
        ctx.lineTo(ocW, ocH - r)
        ctx.quadraticCurveTo(ocW, ocH, ocW - r, ocH)
        ctx.lineTo(r, ocH)
        ctx.quadraticCurveTo(0, ocH, 0, ocH - r)
        ctx.lineTo(0, r)
        ctx.quadraticCurveTo(0, 0, r, 0)
        ctx.closePath()
        ctx.clip()
        // object-fit: cover：按短边填满缩放，裁掉溢出部分
        const scale = Math.max(ocW / iw, ocH / ih)
        const drawW = iw * scale
        const drawH = ih * scale
        const dx = (ocW - drawW) / 2
        const dy = (ocH - drawH) / 2
        ctx.drawImage(nativeImg, dx, dy, drawW, drawH)
        const fabricImg = new fabric.FabricImage(oc, {
          left, top,
          scaleX: width / ocW,
          scaleY: height / ocH,
          selectable: true,
          objectCaching: false,
        })
        ;(fabricImg as any)._blockId = block.id
        ;(fabricImg as any)._blockType = type
        // 图像放入占位图预留的 z 位置。
        const objs = canvasRef.getObjects()
        const slotIndex = objs.indexOf(slot as any)
        canvasRef.remove(slot)
        canvasRef.add(fabricImg)
        if (slotIndex >= 0 && typeof (canvasRef as any).moveObjectTo === 'function') {
          ;(canvasRef as any).moveObjectTo(fabricImg, slotIndex)
        } else if (slotIndex >= 0 && typeof (fabricImg as any).moveTo === 'function') {
          ;(fabricImg as any).moveTo(slotIndex)
        }
        canvasRef.renderAll()
      }
      nativeImg.onerror = () => {
        // 用带标注提示的占位图替换原占位图。
        if (canvasRef) canvasRef.remove(slot)
        addImagePlaceholder(fc!, block, type, left, top, width, height)
      }
      nativeImg.src = src
    } else {
      addImagePlaceholder(fc!, block, type, left, top, width, height)
    }
  }
}

// ---------------------------------------------------------------------------
// Fabric → Blueprint（回写）
// ---------------------------------------------------------------------------

// 累积各页编辑的工作副本，切页时不会丢失改动
const workingBlueprint = ref<any>(null)

function getWorkingBlueprint(): any {
  if (!workingBlueprint.value) {
    workingBlueprint.value = JSON.parse(JSON.stringify(props.blueprint))
  }
  return workingBlueprint.value
}

function canvasToBlueprint(): any {
  if (!fc) return getWorkingBlueprint()
  const bp = JSON.parse(JSON.stringify(getWorkingBlueprint()))
  const page = bp.pages[currentPageIndex.value]
  if (!page) return bp

  // 把 Fabric 填充（字符串或 Gradient）反序列化回 CSS 写回 blueprint
  function serializeFill(fill: any): string {
    if (!fill) return 'transparent'
    if (typeof fill === 'string') return fill
    // Fabric Gradient 对象 → CSS linear-gradient
    if (fill.type === 'linear' && fill.colorStops?.length >= 2) {
      const coords = fill.coords || {}
      const dx = (coords.x2 ?? 1) - (coords.x1 ?? 0)
      const dy = (coords.y2 ?? 0) - (coords.y1 ?? 0)
      const angle = Math.round((Math.atan2(dy, dx) * 180 / Math.PI + 90 + 360) % 360)
      const stops = fill.colorStops
        .map((s: any) => `${s.color} ${Math.round(s.offset * 100)}%`)
        .join(', ')
      return `linear-gradient(${angle}deg, ${stops})`
    }
    return 'transparent'
  }

  const objects = fc.getObjects()
  const canvasBlockIds = new Set<string>()

  // 只回写顶层块：分组内的子块保持 blueprint 原样。
  // 子块的画布对象由 addBlockToCanvas 递归创建，不应写回嵌套子块。
  const blockMap = new Map<string, any>()
  for (const b of page.blocks) {
    if (b.id) blockMap.set(b.id, b)
  }

  // 构建扁平的 id → { block, parentRect } 映射，parentRect 为该块父容器的
  // 归一化（0-1）矩形。保存分组/矩形内的子块时，据此把画布绝对像素坐标
  // 换算回相对父级的归一化坐标。
  type FlatEntry = { block: any; parentRect: { x: number; y: number; w: number; h: number }; parentBlock: any | null }
  const flatMap = new Map<string, FlatEntry>()
  const childIds = new Set<string>()
  function collectBlocksRecursive(
    blocks: any[],
    parentBlock: any | null,
    parentRect: { x: number; y: number; w: number; h: number },
  ) {
    for (const b of blocks) {
      if (!b || !b.id) continue
      if (parentBlock !== null) childIds.add(b.id)
      flatMap.set(b.id, { block: b, parentRect, parentBlock })
      if (b.children?.length) {
        // 取该块自身的归一化矩形，作为其子块的 parentRect。
        const geom = resolveBlockGeometry(b, parentRect)
        const selfRect = { x: geom.bx, y: geom.by, w: geom.bw, h: geom.bh }
        collectBlocksRecursive(b.children, b, selfRect)
      }
    }
  }
  collectBlocksRecursive(page.blocks, null, { x: 0, y: 0, w: 1, h: 1 })

  // 辅助函数：按块的 coordinate_space 回写归一化坐标（子块用相对父级的坐标）。
  function writeBlockXY(block: any, entry: FlatEntry, absPxLeft: number, absPxTop: number) {
    const space = (block.coordinate_space || (entry.parentBlock ? 'parent' : 'page')).toLowerCase()
    const nx = absPxLeft / CANVAS_W
    const ny = absPxTop / CANVAS_H
    if (space === 'parent' && entry.parentRect.w > 0 && entry.parentRect.h > 0) {
      block.x = (nx - entry.parentRect.x) / entry.parentRect.w
      block.y = (ny - entry.parentRect.y) / entry.parentRect.h
    } else {
      block.x = nx
      block.y = ny
    }
  }
  function writeBlockSize(block: any, entry: FlatEntry, absPxW: number, absPxH: number) {
    const space = (block.coordinate_space || (entry.parentBlock ? 'parent' : 'page')).toLowerCase()
    const nw = absPxW / CANVAS_W
    const nh = absPxH / CANVAS_H
    if (space === 'parent' && entry.parentRect.w > 0 && entry.parentRect.h > 0) {
      block.w = nw / entry.parentRect.w
      block.h = nh / entry.parentRect.h
    } else {
      block.w = nw
      block.h = nh
    }
  }

  for (const obj of objects) {
    if ((obj as any)._isGuide) continue
    const blockId = (obj as any)._blockId as string
    const blockType = (obj as any)._blockType as string
    // 跳过辅助对象
    if (!blockId || blockType === 'text_bg' || blockType === 'container') continue
    if (blockType === 'icon') { canvasBlockIds.add(blockId); continue }
    canvasBlockIds.add(blockId)

    // 优先用扁平映射（同时覆盖顶层与嵌套子块），边界情况回退顶层 blockMap。
    const entry = flatMap.get(blockId)
    const block = entry?.block || blockMap.get(blockId)
    if (block) {
      if (blockType === 'line') {
        const line = obj as fabric.Line
        const endpoints = linePageEndpoints(line)
        if (entry) {
          writeBlockXY(block, entry, endpoints.start.x, endpoints.start.y)
          const endX = endpoints.end.x / CANVAS_W
          const endY = endpoints.end.y / CANVAS_H
          const space = (block.coordinate_space || (entry.parentBlock ? 'parent' : 'page')).toLowerCase()
          if (space === 'parent' && entry.parentRect.w > 0 && entry.parentRect.h > 0) {
            block.x2 = (endX - entry.parentRect.x) / entry.parentRect.w
            block.y2 = (endY - entry.parentRect.y) / entry.parentRect.h
          } else {
            block.x2 = endX
            block.y2 = endY
          }
        } else {
          block.x = endpoints.start.x / CANVAS_W
          block.y = endpoints.start.y / CANVAS_H
          block.x2 = endpoints.end.x / CANVAS_W
          block.y2 = endpoints.end.y / CANVAS_H
        }
      } else if (blockType === 'text_box' && obj instanceof fabric.Textbox) {
        const layoutBox = (obj as any)._layoutBox
        const scaleX = obj.scaleX || 1
        const boxLeft = layoutBox
          ? layoutBox.left + (Number(obj.left || 0) - layoutBox.textLeft)
          : Number(obj.left || 0)
        const boxTop = layoutBox
          ? layoutBox.top + (Number(obj.top || 0) - layoutBox.textTop)
          : Number(obj.top || 0)
        const boxWidth = layoutBox
          ? Math.max(1, layoutBox.width + ((Number(obj.width || 0) * scaleX) - layoutBox.textWidth))
          : Number(obj.width || 0) * scaleX
        if (entry) {
          writeBlockXY(block, entry, boxLeft, boxTop)
          writeBlockSize(block, entry, boxWidth, layoutBox?.height || Number(obj.height || 0))
        } else {
          block.x = boxLeft / CANVAS_W
          block.y = boxTop / CANVAS_H
          block.w = boxWidth / CANVAS_W
        }
        // 文本框高度：仅在用户缩放过时保存，否则沿用 blueprint 中的值
        const sy = obj.scaleY || 1
        if (Math.abs(sy - 1) > 0.001) {
          const absH = (obj as any).height * sy
          if (entry) {
            block.h = entry.parentRect.h > 0
              ? (absH / CANVAS_H) / entry.parentRect.h
              : absH / CANVAS_H
          } else {
            block.h = absH / CANVAS_H
          }
        }
        block.content = obj.text || ''
        if (!block.style) block.style = {}
        block.style.font_size = obj.fontSize
        block.style.font_weight = obj.fontWeight === 'bold' ? 'bold' : undefined
        block.style.color = obj.fill as string
        block.style.text_align = obj.textAlign
        if (obj.opacity != null && obj.opacity < 1) {
          block.style.opacity = obj.opacity
        }
      } else {
        if (entry) {
          writeBlockXY(block, entry, obj.left || 0, obj.top || 0)
        } else {
          block.x = (obj.left || 0) / CANVAS_W
          block.y = (obj.top || 0) / CANVAS_H
        }
        const sx = obj.scaleX || 1
        const sy = obj.scaleY || 1
        if (Math.abs(sx - 1) > 0.001 || Math.abs(sy - 1) > 0.001) {
          const absW = (obj as any).width * sx
          const absH = (obj as any).height * sy
          if (entry) {
            writeBlockSize(block, entry, absW, absH)
          } else {
            block.w = absW / CANVAS_W
            block.h = absH / CANVAS_H
          }
        }
        // 保存形状的视觉属性
        if (!block.style) block.style = {}
        block.style.background = serializeFill((obj as any).fill)
        const stroke = (obj as any).stroke
        if (stroke && stroke !== 'transparent') {
          block.style.border_color = stroke
        }
        if (obj.opacity != null && obj.opacity < 1) {
          block.style.opacity = obj.opacity
        }
        if ((obj as any).rx) {
          block.style.border_radius = (obj as any).rx
        }
      }
    } else {
      // 用户新增的元素
      const newBlock: any = {
        id: blockId,
        type: blockType || 'text_box',
        coordinate_space: 'page',
        x: (obj.left || 0) / CANVAS_W,
        y: (obj.top || 0) / CANVAS_H,
        w: ((obj as any).width * (obj.scaleX || 1)) / CANVAS_W,
        h: ((obj as any).height * (obj.scaleY || 1)) / CANVAS_H,
        style: {},
        content: '',
        z_index: 5,
      }
      if (blockType === 'text_box' && obj instanceof fabric.Textbox) {
        newBlock.content = obj.text || ''
        newBlock.style.font_size = obj.fontSize
        newBlock.style.font_weight = obj.fontWeight === 'bold' ? 'bold' : undefined
        newBlock.style.color = obj.fill as string
        newBlock.style.text_align = obj.textAlign
        if (obj.opacity != null && obj.opacity < 1) {
          newBlock.style.opacity = obj.opacity
        }
      }
      if (blockType === 'rectangle' || blockType === 'circle' || blockType === 'group') {
        newBlock.style.background = serializeFill((obj as any).fill)
        if ((obj as any).stroke && (obj as any).stroke !== 'transparent') {
          newBlock.style.border_color = (obj as any).stroke
        }
        if (obj.opacity != null && obj.opacity < 1) {
          newBlock.style.opacity = obj.opacity
        }
        newBlock.style.border_radius = (obj as any).rx || 0
      }
      page.blocks.push(newBlock)
    }
  }

  // 清理已删除的顶层块，保留满足以下任一条件的块：
  // - 已有画布对象（canvasBlockIds）
  // - 含子块（分组容器）
  // - 是已知子块的父级
  page.blocks = page.blocks.filter((b: any) => {
    return canvasBlockIds.has(b.id) || b.children?.length
  })

  // 不改动子块数组：保持 blueprint 的原始结构

  // 写入工作副本，使切换页面时编辑得以保留
  workingBlueprint.value = bp
  return bp
}

function findBlockById(blocks: any[], id: string): any | null {
  for (const b of blocks) {
    if (b.id === id) return b
    if (b.children?.length) {
      const found = findBlockById(b.children, id)
      if (found) return found
    }
  }
  return null
}

// ---------------------------------------------------------------------------
// 动作
// ---------------------------------------------------------------------------
function goToPage(index: number) {
  if (index < 0 || index >= pages.value.length) return
  // 切页前先保存当前页的改动
  saveCurrentPageToBlueprint()
  currentPageIndex.value = index
  loadPageToCanvas(index)
}

function saveCurrentPageToBlueprint() {
  if (!fc) return
  canvasToBlueprint() // updates workingBlueprint
  pages.value = getWorkingBlueprint().pages || []
}

const saveMessage = ref('')

async function doSave(): Promise<any> {
  const bp = canvasToBlueprint()
  const blueprintPath = props.blueprintObjectPath
  if (!blueprintPath) throw new Error('Blueprint path not found')

  await saveBlueprint({
    user_id: props.userId || 'anonymous',
    blueprint_object_path: blueprintPath,
    blueprint: bp,
  })
  emit('saved', bp)
  return bp
}

async function handleSave() {
  saving.value = true
  saveMessage.value = ''
  try {
    await doSave()
    saveMessage.value = t('ppt.saved')
    setTimeout(() => { saveMessage.value = '' }, 2000)
  } catch (err: any) {
    console.error('Save failed:', err)
    saveMessage.value = t('ppt.save_failed')
  } finally {
    saving.value = false
  }
}

async function handleExport() {
  exporting.value = true
  try {
    // 先保存，再从保存后的 blueprint 导出
    const bp = await doSave()

    const result = await renderPresentationPptx({
      user_id: props.userId || 'anonymous',
      blueprint_object_path: props.blueprintObjectPath,
      filename: `${bp.deck_goal || 'presentation'}.pptx`,
      title: bp.deck_goal || 'PPT',
    })

    if (result?.url) {
      window.open(result.url, '_blank')
    }
    emit('exported', result)
  } catch (err: any) {
    console.error('Export failed:', err)
    alert(t('ppt.export_failed', { message: err.message || err }))
  } finally {
    exporting.value = false
  }
}

// ---------------------------------------------------------------------------
// 工具栏：监听选中状态
// ---------------------------------------------------------------------------
function syncToolbarFromSelection() {
  const obj = fc?.getActiveObject()
  selectedObj.value = obj || null
  selectedIsText.value = obj instanceof fabric.Textbox
  selectedIsShape.value = !!(obj && !selectedIsText.value && !(obj as any)._isGuide)

  if (obj instanceof fabric.Textbox) {
    toolFontSize.value = obj.fontSize || 18
    toolColor.value = (obj.fill as string) || '#222222'
    toolBold.value = obj.fontWeight === 'bold'
    toolAlign.value = obj.textAlign || 'left'
  }
  if (selectedIsShape.value && obj) {
    const fill = (obj as any).fill
    if (fill && typeof fill === 'object' && fill.type === 'linear') {
      // 是渐变
      toolFillMode.value = 'gradient'
      const stops = fill.colorStops || []
      toolGradientFrom.value = stops[0]?.color || '#3b82f6'
      toolGradientTo.value = stops[stops.length - 1]?.color || '#8b5cf6'
      // 从坐标粗略推算角度
      const coords = fill.coords || {}
      const dx = (coords.x2 ?? 1) - (coords.x1 ?? 0)
      const dy = (coords.y2 ?? 0) - (coords.y1 ?? 0)
      toolGradientAngle.value = Math.round((Math.atan2(dy, dx) * 180 / Math.PI + 90 + 360) % 360)
    } else {
      toolFillMode.value = 'solid'
      toolFillColor.value = (typeof fill === 'string' && fill !== 'transparent') ? fill : '#ffffff'
    }
    const stroke = (obj as any).stroke
    toolStrokeColor.value = (typeof stroke === 'string' && stroke !== 'transparent') ? stroke : '#000000'
    toolOpacity.value = Math.round((obj.opacity ?? 1) * 100)
    toolCornerRadius.value = (obj as any).rx || 0
  }
}

function onSelectionCreated() { syncToolbarFromSelection() }

function onSelectionCleared() {
  selectedObj.value = null
  selectedIsText.value = false
  selectedIsShape.value = false
}

function applyFontSize(size: number) {
  const obj = fc?.getActiveObject()
  if (obj instanceof fabric.Textbox) {
    obj.set('fontSize', size)
    fc?.renderAll()
  }
}

function applyColor(color: string) {
  const obj = fc?.getActiveObject()
  if (obj instanceof fabric.Textbox) {
    obj.set('fill', color)
    fc?.renderAll()
  }
}

function applyBold(bold: boolean) {
  const obj = fc?.getActiveObject()
  if (obj instanceof fabric.Textbox) {
    obj.set('fontWeight', bold ? 'bold' : 'normal')
    fc?.renderAll()
  }
}

function applyAlign(align: string) {
  const obj = fc?.getActiveObject()
  if (obj instanceof fabric.Textbox) {
    obj.set('textAlign', align)
    fc?.renderAll()
  }
}

watch(toolFontSize, applyFontSize)
watch(toolColor, applyColor)
watch(toolBold, applyBold)
watch(toolAlign, applyAlign)

function applyFillColor(color: string) {
  const obj = fc?.getActiveObject()
  if (obj && !(obj instanceof fabric.Textbox) && toolFillMode.value === 'solid') {
    obj.set('fill', color)
    fc?.renderAll()
  }
}

function applyGradientFill() {
  const obj = fc?.getActiveObject()
  if (!obj || !fc || obj instanceof fabric.Textbox) return
  const w = (obj.width || 1) * (obj.scaleX || 1)
  const h = (obj.height || 1) * (obj.scaleY || 1)
  const coords = angleToCoordsPercent(toolGradientAngle.value)
  const gradient = new fabric.Gradient({
    type: 'linear',
    gradientUnits: 'percentage',
    coords: { x1: coords.x1, y1: coords.y1, x2: coords.x2, y2: coords.y2 },
    colorStops: [
      { offset: 0, color: toolGradientFrom.value },
      { offset: 1, color: toolGradientTo.value },
    ],
  })
  obj.set('fill', gradient)
  fc.renderAll()
}
function applyStrokeColor(color: string) {
  const obj = fc?.getActiveObject()
  if (obj && !(obj instanceof fabric.Textbox)) {
    obj.set('stroke', color)
    if (!obj.strokeWidth) obj.set('strokeWidth', 1)
    fc?.renderAll()
  }
}
function applyOpacity(val: number) {
  const obj = fc?.getActiveObject()
  if (obj) {
    obj.set('opacity', Math.max(0, Math.min(100, val)) / 100)
    fc?.renderAll()
  }
}
function applyCornerRadius(val: number) {
  const obj = fc?.getActiveObject()
  if (obj && 'rx' in obj) {
    (obj as any).set('rx', val);
    (obj as any).set('ry', val)
    fc?.renderAll()
  }
}

function startAngleDrag(e: MouseEvent) {
  e.preventDefault()
  const el = e.currentTarget as HTMLElement
  const rect = el.getBoundingClientRect()
  const cx = rect.left + rect.width / 2
  const cy = rect.top + rect.height / 2

  function calcAngle(ex: number, ey: number) {
    const deg = Math.round(Math.atan2(ex - cx, -(ey - cy)) * 180 / Math.PI + 360) % 360
    // 接近时吸附到 45 度整数倍
    const snap45 = Math.round(deg / 45) * 45
    toolGradientAngle.value = Math.abs(deg - snap45) < 8 ? snap45 % 360 : deg
  }

  function onMove(ev: MouseEvent) { calcAngle(ev.clientX, ev.clientY) }
  function onUp() {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  calcAngle(e.clientX, e.clientY)
}

watch(toolFillColor, applyFillColor)
watch(toolStrokeColor, applyStrokeColor)
watch(toolOpacity, applyOpacity)
watch(toolCornerRadius, applyCornerRadius)
watch(toolFillMode, (mode) => {
  if (mode === 'gradient') applyGradientFill()
  else applyFillColor(toolFillColor.value)
})
watch(toolGradientFrom, () => { if (toolFillMode.value === 'gradient') applyGradientFill() })
watch(toolGradientTo, () => { if (toolFillMode.value === 'gradient') applyGradientFill() })
watch(toolGradientAngle, () => { if (toolFillMode.value === 'gradient') applyGradientFill() })

// ---------------------------------------------------------------------------
// 增删元素
// ---------------------------------------------------------------------------
let blockCounter = 1000

function addText() {
  if (!fc) return
  const id = `user_text_${blockCounter++}`
  const tb = new fabric.Textbox(t('ppt.new_text'), {
    left: 100, top: 100, width: 300,
    fontSize: 24,
    fill: '#222222',
    fontFamily: "'PingFang SC', 'Microsoft YaHei', sans-serif",
    editable: true,
    selectable: true,
  })
  ;(tb as any)._blockId = id
  ;(tb as any)._blockType = 'text_box'
  fc.add(tb)
  fc.setActiveObject(tb)
  fc.renderAll()
}

function addRect() {
  if (!fc) return
  const id = `user_rect_${blockCounter++}`
  const rect = new fabric.Rect({
    left: 100, top: 100, width: 200, height: 120,
    fill: '#ffffff',
    stroke: '#e2e8f0',
    strokeWidth: 1,
    rx: 8, ry: 8,
    selectable: true,
  })
  ;(rect as any)._blockId = id
  ;(rect as any)._blockType = 'rectangle'
  fc.add(rect)
  fc.setActiveObject(rect)
  fc.renderAll()
}

function deleteSelected() {
  if (!fc) return
  const obj = fc.getActiveObject()
  if (!obj) return
  // 用户正在编辑文本时不做删除
  if (obj instanceof fabric.Textbox && obj.isEditing) return
  fc.remove(obj)
  selectedObj.value = null
  fc.renderAll()
}

// ---------------------------------------------------------------------------
// 撤销 / 重做
// ---------------------------------------------------------------------------
const undoStack: string[] = []
const redoStack: string[] = []
let _undoLock = false

function saveUndoState() {
  if (!fc || _undoLock) return
  const json = JSON.stringify(fc.toJSON())
  // 不压入重复项
  if (undoStack.length > 0 && undoStack[undoStack.length - 1] === json) return
  undoStack.push(json)
  if (undoStack.length > 50) undoStack.shift()
  redoStack.length = 0
}

async function undo() {
  if (!fc || undoStack.length === 0) return
  const current = JSON.stringify(fc.toJSON())
  redoStack.push(current)
  const prev = undoStack.pop()!
  _undoLock = true
  await fc.loadFromJSON(prev)
  fc.renderAll()
  _undoLock = false
}

async function redo() {
  if (!fc || redoStack.length === 0) return
  const current = JSON.stringify(fc.toJSON())
  undoStack.push(current)
  const next = redoStack.pop()!
  _undoLock = true
  await fc.loadFromJSON(next)
  fc.renderAll()
  _undoLock = false
}

function onKeyDown(e: KeyboardEvent) {
  if (!fc || isFullscreen.value) return
  const active = fc.getActiveObject()
  if (active instanceof fabric.Textbox && active.isEditing) return

  const isCtrlOrCmd = e.ctrlKey || e.metaKey
  const step = e.shiftKey ? 10 : 1

  // 撤销：Ctrl+Z / Cmd+Z
  if (isCtrlOrCmd && e.key === 'z' && !e.shiftKey) {
    e.preventDefault()
    undo()
    return
  }
  // 重做：Ctrl+Shift+Z / Cmd+Shift+Z 或 Ctrl+Y
  if ((isCtrlOrCmd && e.key === 'z' && e.shiftKey) || (isCtrlOrCmd && e.key === 'y')) {
    e.preventDefault()
    redo()
    return
  }

  // 删除
  if (e.key === 'Delete' || e.key === 'Backspace') {
    e.preventDefault()
    saveUndoState()
    deleteSelected()
    return
  }

  // 方向键：移动选中对象
  if (!active) return
  if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
    e.preventDefault()
    saveUndoState()
    switch (e.key) {
      case 'ArrowLeft':  active.set('left', (active.left || 0) - step); break
      case 'ArrowRight': active.set('left', (active.left || 0) + step); break
      case 'ArrowUp':    active.set('top', (active.top || 0) - step); break
      case 'ArrowDown':  active.set('top', (active.top || 0) + step); break
    }
    active.setCoords()
    fc.renderAll()
  }
}

// ---------------------------------------------------------------------------
// 智能对齐参考线
// ---------------------------------------------------------------------------
const SNAP_THRESHOLD = 8 // px in logical coords — how close before snapping
const guideLines: fabric.Line[] = []

function clearGuides() {
  if (!fc) return
  for (const line of guideLines) {
    fc.remove(line)
  }
  guideLines.length = 0
}

function addGuideLine(x1: number, y1: number, x2: number, y2: number) {
  if (!fc) return
  const line = new fabric.Line([x1, y1, x2, y2], {
    stroke: '#f43f5e',
    strokeWidth: 1,
    strokeDashArray: [4, 3],
    selectable: false,
    evented: false,
    excludeFromExport: true,
  })
  ;(line as any)._isGuide = true
  guideLines.push(line)
  fc.add(line)
}

function onObjectMoving(e: any) {
  if (!fc) return
  const target = e.target as fabric.FabricObject
  if (!target || (target as any)._isGuide) return

  clearGuides()

  const tl = target.left || 0
  const tt = target.top || 0
  const tw = (target.width || 0) * (target.scaleX || 1)
  const th = (target.height || 0) * (target.scaleY || 1)
  const tCx = tl + tw / 2
  const tCy = tt + th / 2
  const tRight = tl + tw
  const tBottom = tt + th

  // 画布中心参考线
  const cCx = CANVAS_W / 2
  const cCy = CANVAS_H / 2

  let snappedX = false
  let snappedY = false

  // 水平吸附到画布中心
  if (Math.abs(tCx - cCx) < SNAP_THRESHOLD) {
    target.set('left', cCx - tw / 2)
    addGuideLine(cCx, 0, cCx, CANVAS_H)
    snappedX = true
  }
  // 垂直吸附到画布中心
  if (Math.abs(tCy - cCy) < SNAP_THRESHOLD) {
    target.set('top', cCy - th / 2)
    addGuideLine(0, cCy, CANVAS_W, cCy)
    snappedY = true
  }

  // 收集其他对象的边缘与中心，用于对齐与间距计算。
  // 只纳入可选且有意义的形状——跳过参考线、文本背景辅助对象、
  // 不可选装饰、整页背景以及细小的子标签。
  const canvasArea = CANVAS_W * CANVAS_H
  const targetType = (target as any)._blockType || ''
  const others: { left: number; top: number; right: number; bottom: number; cx: number; cy: number }[] = []
  for (const obj of fc.getObjects()) {
    if (obj === target) continue
    if ((obj as any)._isGuide) continue
    if ((obj as any)._blockType === 'text_bg') continue
    if (!obj.selectable) continue
    const ol = obj.left || 0
    const ot = obj.top || 0
    const ow = (obj.width || 0) * (obj.scaleX || 1)
    const oh = (obj.height || 0) * (obj.scaleY || 1)
    if (ow < 1 || oh < 1) continue
    // 跳过整页背景矩形
    if (ow * oh >= canvasArea * 0.8) continue
    others.push({ left: ol, top: ot, right: ol + ow, bottom: ot + oh, cx: ol + ow / 2, cy: ot + oh / 2 })
  }

  // 间距检测只使用可选且非过小的对象
  // (at least 20px in both dimensions) — avoids icons/labels interfering.
  const spacingPeers = others.filter(o => {
    const ow = o.right - o.left
    const oh = o.bottom - o.top
    return ow >= 20 && oh >= 20
  })

  const updatedLeft = target.left || 0
  const updatedTop = target.top || 0
  const updatedCx = updatedLeft + tw / 2
  const updatedCy = updatedTop + th / 2
  const updatedRight = updatedLeft + tw
  const updatedBottom = updatedTop + th

  for (const o of others) {
    if (!snappedX) {
      // 左 ↔ 左
      if (Math.abs(updatedLeft - o.left) < SNAP_THRESHOLD) {
        target.set('left', o.left)
        addGuideLine(o.left, 0, o.left, CANVAS_H)
        snappedX = true
      }
      // 右 ↔ 右
      else if (Math.abs(updatedRight - o.right) < SNAP_THRESHOLD) {
        target.set('left', o.right - tw)
        addGuideLine(o.right, 0, o.right, CANVAS_H)
        snappedX = true
      }
      // 左 ↔ 右
      else if (Math.abs(updatedLeft - o.right) < SNAP_THRESHOLD) {
        target.set('left', o.right)
        addGuideLine(o.right, 0, o.right, CANVAS_H)
        snappedX = true
      }
      // 右 ↔ 左
      else if (Math.abs(updatedRight - o.left) < SNAP_THRESHOLD) {
        target.set('left', o.left - tw)
        addGuideLine(o.left, 0, o.left, CANVAS_H)
        snappedX = true
      }
      // 中心 ↔ 中心 X
      else if (Math.abs(updatedCx - o.cx) < SNAP_THRESHOLD) {
        target.set('left', o.cx - tw / 2)
        addGuideLine(o.cx, 0, o.cx, CANVAS_H)
        snappedX = true
      }
    }

    if (!snappedY) {
      // 上 ↔ 上
      if (Math.abs(updatedTop - o.top) < SNAP_THRESHOLD) {
        target.set('top', o.top)
        addGuideLine(0, o.top, CANVAS_W, o.top)
        snappedY = true
      }
      // 下 ↔ 下
      else if (Math.abs(updatedBottom - o.bottom) < SNAP_THRESHOLD) {
        target.set('top', o.bottom - th)
        addGuideLine(0, o.bottom, CANVAS_W, o.bottom)
        snappedY = true
      }
      // 上 ↔ 下
      else if (Math.abs(updatedTop - o.bottom) < SNAP_THRESHOLD) {
        target.set('top', o.bottom)
        addGuideLine(0, o.bottom, CANVAS_W, o.bottom)
        snappedY = true
      }
      // 下 ↔ 上
      else if (Math.abs(updatedBottom - o.top) < SNAP_THRESHOLD) {
        target.set('top', o.top - th)
        addGuideLine(0, o.top, CANVAS_W, o.top)
        snappedY = true
      }
      // 中心 ↔ 中心 Y
      else if (Math.abs(updatedCy - o.cy) < SNAP_THRESHOLD) {
        target.set('top', o.cy - th / 2)
        addGuideLine(0, o.cy, CANVAS_W, o.cy)
        snappedY = true
      }
    }

    if (snappedX && snappedY) break
  }

  // --- Equal spacing guides (runs independently from edge snapping) ---
  const curLeft = target.left || 0
  const curTop = target.top || 0
  const curRight = curLeft + tw
  const curBottom = curTop + th
  let spacingSnappedX = false
  let spacingSnappedY = false

  const curCx = curLeft + tw / 2
  const curCy = curTop + th / 2
  const yOverlap = (o: typeof others[0]) => o.bottom > curTop + 5 && o.top < curBottom - 5
  const toLeft = spacingPeers.filter(o => o.cx < curCx && yOverlap(o)).sort((a, b) => b.right - a.right)
  const toRight = spacingPeers.filter(o => o.cx > curCx && yOverlap(o)).sort((a, b) => a.left - b.left)

  // 两个相邻对象之间：吸附到等间距
  if (toLeft.length > 0 && toRight.length > 0 && !spacingSnappedX) {
    const nearL = toLeft[0]
    const nearR = toRight[0]
    const gapL = curLeft - nearL.right
    const gapR = nearR.left - curRight
    if (gapL > 2 && gapR > 2 && Math.abs(gapL - gapR) < SNAP_THRESHOLD) {
      const avgGap = (nearL.right + nearR.left - tw) / 2
      target.set('left', avgGap)
      spacingSnappedX = true
      const markY = Math.max(nearL.top, curTop, nearR.top) +
        Math.min(nearL.bottom - nearL.top, th, nearR.bottom - nearR.top) / 2
      const snapLeft = avgGap
      const snapRight = avgGap + tw
      addGuideLine(nearL.right, markY, snapLeft, markY)
      addGuideLine(nearL.right, markY - 6, nearL.right, markY + 6)
      addGuideLine(snapLeft, markY - 6, snapLeft, markY + 6)
      addGuideLine(snapRight, markY, nearR.left, markY)
      addGuideLine(snapRight, markY - 6, snapRight, markY + 6)
      addGuideLine(nearR.left, markY - 6, nearR.left, markY + 6)
    }
  }

  // 末端位置：匹配左侧两个相邻对象的间距
  if (toLeft.length >= 2 && !spacingSnappedX) {
    const a = toLeft[1]
    const b = toLeft[0]
    const existingGap = b.left - a.right
    const myGap = curLeft - b.right
    if (existingGap > 2 && Math.abs(myGap - existingGap) < SNAP_THRESHOLD) {
      target.set('left', b.right + existingGap)
      spacingSnappedX = true
      const markY = Math.max(a.top, b.top, curTop) + Math.min(a.bottom - a.top, b.bottom - b.top, th) / 2
      addGuideLine(a.right, markY, b.left, markY)
      addGuideLine(a.right, markY - 6, a.right, markY + 6)
      addGuideLine(b.left, markY - 6, b.left, markY + 6)
      addGuideLine(b.right, markY, b.right + existingGap, markY)
      addGuideLine(b.right, markY - 6, b.right, markY + 6)
      addGuideLine(b.right + existingGap, markY - 6, b.right + existingGap, markY + 6)
    }
  }
  // 起始位置：匹配右侧两个相邻对象的间距
  if (toRight.length >= 2 && !spacingSnappedX) {
    const a = toRight[0]
    const b = toRight[1]
    const existingGap = b.left - a.right
    const myGap = a.left - curRight
    if (existingGap > 2 && Math.abs(myGap - existingGap) < SNAP_THRESHOLD) {
      target.set('left', a.left - existingGap - tw)
      spacingSnappedX = true
      const markY = Math.max(a.top, b.top, curTop) + Math.min(a.bottom - a.top, b.bottom - b.top, th) / 2
      addGuideLine(a.right, markY, b.left, markY)
      addGuideLine(a.right, markY - 6, a.right, markY + 6)
      addGuideLine(b.left, markY - 6, b.left, markY + 6)
      const snapRight = a.left - existingGap
      addGuideLine(snapRight, markY, a.left, markY)
      addGuideLine(snapRight, markY - 6, snapRight, markY + 6)
      addGuideLine(a.left, markY - 6, a.left, markY + 6)
    }
  }

  // 垂直等间距
  const xOverlap = (o: typeof others[0]) => o.right > curLeft + 5 && o.left < curRight - 5
  const above = spacingPeers.filter(o => o.cy < curTop && xOverlap(o)).sort((a, b) => b.bottom - a.bottom)
  const below = spacingPeers.filter(o => o.cy > curBottom && xOverlap(o)).sort((a, b) => a.top - b.top)

  if (above.length > 0 && below.length > 0 && !spacingSnappedY) {
    const nearA = above[0]
    const nearB = below[0]
    const gapA = curTop - nearA.bottom
    const gapB = nearB.top - curBottom
    if (gapA > 2 && gapB > 2 && Math.abs(gapA - gapB) < SNAP_THRESHOLD) {
      const avgTop = (nearA.bottom + nearB.top - th) / 2
      target.set('top', avgTop)
      spacingSnappedY = true
      const markX = Math.max(nearA.left, curLeft, nearB.left) +
        Math.min(nearA.right - nearA.left, tw, nearB.right - nearB.left) / 2
      const snapTop = avgTop
      const snapBottom = avgTop + th
      addGuideLine(markX, nearA.bottom, markX, snapTop)
      addGuideLine(markX - 6, nearA.bottom, markX + 6, nearA.bottom)
      addGuideLine(markX - 6, snapTop, markX + 6, snapTop)
      addGuideLine(markX, snapBottom, markX, nearB.top)
      addGuideLine(markX - 6, snapBottom, markX + 6, snapBottom)
      addGuideLine(markX - 6, nearB.top, markX + 6, nearB.top)
    }
  }

  if (above.length >= 2 && !spacingSnappedY) {
    const a = above[1]; const b = above[0]
    const existingGap = b.top - a.bottom
    const myGap = curTop - b.bottom
    if (existingGap > 2 && Math.abs(myGap - existingGap) < SNAP_THRESHOLD) {
      target.set('top', b.bottom + existingGap)
      spacingSnappedY = true
      const markX = Math.max(a.left, b.left, curLeft) + Math.min(a.right - a.left, b.right - b.left, tw) / 2
      addGuideLine(markX, a.bottom, markX, b.top)
      addGuideLine(markX - 6, a.bottom, markX + 6, a.bottom)
      addGuideLine(markX - 6, b.top, markX + 6, b.top)
      addGuideLine(markX, b.bottom, markX, b.bottom + existingGap)
      addGuideLine(markX - 6, b.bottom, markX + 6, b.bottom)
      addGuideLine(markX - 6, b.bottom + existingGap, markX + 6, b.bottom + existingGap)
    }
  }
  if (below.length >= 2 && !spacingSnappedY) {
    const a = below[0]; const b = below[1]
    const existingGap = b.top - a.bottom
    const myGap = a.top - curBottom
    if (existingGap > 2 && Math.abs(myGap - existingGap) < SNAP_THRESHOLD) {
      target.set('top', a.top - existingGap - th)
      spacingSnappedY = true
      const markX = Math.max(a.left, b.left, curLeft) + Math.min(a.right - a.left, b.right - b.left, tw) / 2
      addGuideLine(markX, a.bottom, markX, b.top)
      addGuideLine(markX - 6, a.bottom, markX + 6, a.bottom)
      addGuideLine(markX - 6, b.top, markX + 6, b.top)
      const snapBottom = a.top - existingGap
      addGuideLine(markX, snapBottom, markX, a.top)
      addGuideLine(markX - 6, snapBottom, markX + 6, snapBottom)
      addGuideLine(markX - 6, a.top, markX + 6, a.top)
    }
  }

  target.setCoords()
  fc.renderAll()
}

function onObjectModified() {
  clearGuides()
  fc?.renderAll()
}

// ---------------------------------------------------------------------------
// 全屏预览
// ---------------------------------------------------------------------------
const isFullscreen = ref(false)
const fsPageIndex = ref(0)
const fsCanvasEl = ref<HTMLCanvasElement | null>(null)
let fsFc: fabric.StaticCanvas | null = null

const fsOverlayEl = ref<HTMLDivElement | null>(null)

function enterFullscreen() {
  saveCurrentPageToBlueprint()
  isFullscreen.value = true
  fsPageIndex.value = currentPageIndex.value
  nextTick(() => {
    // 在浮层元素上请求原生全屏
    fsOverlayEl.value?.requestFullscreen?.().catch(() => {})
    renderFsPage(fsPageIndex.value)
  })
}

function exitFullscreen() {
  isFullscreen.value = false
  if (fsFc) { fsFc.dispose(); fsFc = null }
  // 若处于全屏则退出
  if (document.fullscreenElement) {
    document.exitFullscreen?.().catch(() => {})
  }
}

function renderFsPage(idx: number) {
  if (!fsCanvasEl.value) return
  if (fsFc) { fsFc.dispose(); fsFc = null }

  const vw = window.innerWidth
  const vh = window.innerHeight
  const scale = Math.min(vw / CANVAS_W, vh / CANVAS_H)
  const dpr = window.devicePixelRatio || 1

  fsFc = new fabric.StaticCanvas(fsCanvasEl.value, {
    width: CANVAS_W,
    height: CANVAS_H,
    enableRetinaScaling: false,
  })
  fsFc.setDimensions({ width: Math.floor(CANVAS_W * scale), height: Math.floor(CANVAS_H * scale) }, { cssOnly: true } as any)
  fsFc.setDimensions({ width: Math.floor(CANVAS_W * scale * dpr), height: Math.floor(CANVAS_H * scale * dpr) }, { backstoreOnly: true } as any)
  fsFc.setZoom(scale * dpr)

  // 把该页重新渲染到静态画布
  const bp = getWorkingBlueprint()
  const page = bp.pages?.[idx]
  if (!page) return

  const pageBg = page.style?.background || page.style?.background_color || bp.theme?.page_background || '#ffffff'
  const bgParsed = parseLinearGradient(pageBg)
  if (bgParsed) {
    const bgRect = new fabric.Rect({
      left: 0, top: 0, width: CANVAS_W, height: CANVAS_H,
      fill: makeFabricGradient(pageBg, CANVAS_W, CANVAS_H),
      selectable: false, evented: false,
    })
    fsFc.add(bgRect)
  } else {
    fsFc.backgroundColor = isTransparent(pageBg) ? '#ffffff' : pageBg
  }

  // 临时替换 fc，使 addBlockToCanvas 能在全屏画布上工作
  const origFc = fc
  fc = fsFc as any
  const blocks = [...(page.blocks || [])].sort((a: any, b: any) => (a.z_index || 0) - (b.z_index || 0))
  for (const block of blocks) {
    addBlockToCanvas(block, { x: 0, y: 0, w: 1, h: 1 })
  }
  fc = origFc
  fsFc.renderAll()
}

function fsNext() {
  const bp = getWorkingBlueprint()
  if (fsPageIndex.value < (bp.pages?.length || 0) - 1) {
    fsPageIndex.value++
    renderFsPage(fsPageIndex.value)
  }
}

function fsPrev() {
  if (fsPageIndex.value > 0) {
    fsPageIndex.value--
    renderFsPage(fsPageIndex.value)
  }
}

function onFsKeyDown(e: KeyboardEvent) {
  if (!isFullscreen.value) return
  if (e.key === 'Escape') { e.preventDefault(); exitFullscreen() }
  else if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === ' ') { e.preventDefault(); fsNext() }
  else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); fsPrev() }
}

// ---------------------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------------------
function recalcScale() {
  if (!canvasWrapperEl.value || !fc) return
  const padding = 32
  const w = canvasWrapperEl.value.clientWidth - padding
  const h = canvasWrapperEl.value.clientHeight - padding
  if (w <= 0 || h <= 0) return
  const newScale = Math.min(w / CANVAS_W, h / CANVAS_H, 1)
  displayScale.value = newScale

  const dpr = window.devicePixelRatio || 1
  const cssW = Math.floor(CANVAS_W * newScale)
  const cssH = Math.floor(CANVAS_H * newScale)

  // 设置 CSS 显示尺寸
  fc.setDimensions({ width: cssW, height: cssH }, { cssOnly: true } as any)
  // 以完整 retina 分辨率设置绘图缓冲
  fc.setDimensions({ width: cssW * dpr, height: cssH * dpr }, { backstoreOnly: true } as any)
  // 缩放同时计入布局缩放与 retina 倍率
  fc.setZoom(newScale * dpr)
}

function onGlobalMouseUp(_ev: MouseEvent) {
  // 若全局 mouseup 触发时 fabric 仍处于拖拽状态，强制结束拖拽。
  // 这能救回那些被拖到画布边缘之外、真实的
  // mouseup 不会再派发给画布 DOM 元素的对象。
  if (!fc) return
  const f: any = fc as any
  const stuckDragging =
    f._currentTransform != null ||
    f._isCurrentlyDrawing === true ||
    f.__isDragging === true
  if (!stuckDragging) return
  try {
    // Fabric v6 由 __onMouseUp 处理指针释放；v5 则回退到私有 setter。
    if (typeof f.__onMouseUp === 'function') {
      f.__onMouseUp(_ev)
    } else {
      f._currentTransform = null
      f.setCursor(f.defaultCursor || 'default')
      const active = f.getActiveObject()
      if (active) active.setCoords()
      f.requestRenderAll?.()
    }
  } catch {
    // 忽略——这是尽力而为的补救。
    try { (fc as any)._currentTransform = null } catch {}
    try { fc?.requestRenderAll?.() } catch {}
  }
}

onMounted(async () => {
  await nextTick()
  if (!canvasEl.value) return

  fc = new fabric.Canvas(canvasEl.value, {
    width: CANVAS_W,
    height: CANVAS_H,
    selection: true,
    preserveObjectStacking: true,
    enableRetinaScaling: false, // we handle DPI manually in recalcScale
  })
  fc.on('selection:created', onSelectionCreated)
  fc.on('selection:updated', onSelectionCreated)
  fc.on('selection:cleared', onSelectionCleared)
  fc.on('object:moving', onObjectMoving)
  fc.on('object:modified', () => { saveUndoState(); onObjectModified() })
  fc.on('mouse:up', () => { clearGuides(); fc?.renderAll() })
  fc.on('text:changed', saveUndoState)

  // 回退：把任意全局 mouseup 转发给 fabric，即便
  // 用户在画布元素之外松开鼠标（例如把对象
  // 拖到画布边缘之后）也能终止拖拽。否则 fabric 的拖拽状态
  // 会卡住，对象一直跟随光标。
  document.addEventListener('mouseup', onGlobalMouseUp, true)
  document.addEventListener('mouseleave', onGlobalMouseUp, true)

  workingBlueprint.value = JSON.parse(JSON.stringify(props.blueprint))
  // 修正被写坏的 coordinate_space：分组内的子块必须是 'parent'，
  // 而不是 'page'。此前版本的编辑器曾错误地强制写成 'page'。
  for (const page of (workingBlueprint.value?.pages || [])) {
    for (const block of (page.blocks || [])) {
      if (block.children?.length) {
        for (const child of block.children) {
          if (child.coordinate_space === 'page') {
            child.coordinate_space = 'parent'
          }
        }
      }
    }
  }
  pages.value = workingBlueprint.value?.pages || []
  if (pages.value.length > 0) {
    loadPageToCanvas(0)
    nextTick(() => saveUndoState())
  }

  // 让画布自适应容器
  if (canvasWrapperEl.value) {
    resizeOb = new ResizeObserver(recalcScale)
    resizeOb.observe(canvasWrapperEl.value)
  }
  recalcScale()
  document.addEventListener('keydown', onKeyDown)
  document.addEventListener('keydown', onFsKeyDown)
  document.addEventListener('fullscreenchange', onFullscreenChange)
})

function onFullscreenChange() {
  // 若用户已退出原生全屏（例如按 ESC），则同时关闭本浮层
  if (!document.fullscreenElement && isFullscreen.value) {
    isFullscreen.value = false
    if (fsFc) { fsFc.dispose(); fsFc = null }
  }
}

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeyDown)
  document.removeEventListener('keydown', onFsKeyDown)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  document.removeEventListener('mouseup', onGlobalMouseUp, true)
  document.removeEventListener('mouseleave', onGlobalMouseUp, true)
  if (fsFc) { fsFc.dispose(); fsFc = null }
  resizeOb?.disconnect()
  resizeOb = null
  if (fc) {
    fc.dispose()
    fc = null
  }
})
</script>

<template>
  <div class="pe-overlay">
    <!-- Left sidebar: slide list -->
    <aside class="pe-sidebar">
      <div class="pe-sidebar-header">
        <span>{{ t('ppt.pages') }}</span>
        <span class="pe-badge">{{ totalPages }}</span>
      </div>
      <div class="pe-slide-list">
        <div
          v-for="(page, idx) in pages"
          :key="page.page_id || idx"
          class="pe-slide-item"
          :class="{ 'pe-slide-active': idx === currentPageIndex }"
          @click="goToPage(idx)"
        >
          <div class="pe-slide-num">{{ idx + 1 }}</div>
          <div class="pe-slide-info">
            <div class="pe-slide-title">{{ page.page_title || t('ppt.page', { index: idx + 1 }) }}</div>
          </div>
        </div>
      </div>
    </aside>

    <!-- Main area -->
    <div class="pe-main">
      <!-- Toolbar -->
      <div class="pe-toolbar">
        <!-- Property tools -->
        <div class="pe-tool-group">
          <!-- Text tools -->
          <template v-if="selectedIsText">
            <div class="pe-tool-item">
              <span class="pe-tool-label">{{ t('ppt.size') }}</span>
              <input v-model.number="toolFontSize" type="number" min="8" max="120" class="pe-input-num w-14" />
            </div>
            <div class="pe-tool-item">
              <span class="pe-tool-label">{{ t('ppt.color') }}</span>
              <label class="pe-color-swatch" :style="{ background: toolColor }">
                <input v-model="toolColor" type="color" class="sr-only" />
              </label>
            </div>
            <div class="pe-tool-item">
              <button class="pe-btn-icon" :class="{ 'pe-btn-active': toolBold }" @click="toolBold = !toolBold" :title="t('ppt.bold')">
                <b>B</b>
              </button>
            </div>
            <div class="pe-tool-item">
              <div class="pe-btn-group">
                <button v-for="a in ['left', 'center', 'right']" :key="a"
                  class="pe-btn-icon" :class="{ 'pe-btn-active': toolAlign === a }" @click="toolAlign = a" :title="t('ppt.align.' + a)">
                  <svg v-if="a==='left'" width="14" height="14" viewBox="0 0 14 14"><path d="M2 3h10M2 6h6M2 9h8M2 12h4" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg>
                  <svg v-else-if="a==='center'" width="14" height="14" viewBox="0 0 14 14"><path d="M2 3h10M4 6h6M3 9h8M5 12h4" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg>
                  <svg v-else width="14" height="14" viewBox="0 0 14 14"><path d="M2 3h10M6 6h6M4 9h8M8 12h4" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg>
                </button>
              </div>
            </div>
          </template>
          <!-- Shape tools -->
          <template v-else-if="selectedIsShape">
            <div class="pe-tool-item">
              <div class="pe-btn-group">
                <button class="pe-btn-sm" :class="{ 'pe-btn-active': toolFillMode === 'solid' }" @click="toolFillMode = 'solid'">{{ t('ppt.fill.solid') }}</button>
                <button class="pe-btn-sm" :class="{ 'pe-btn-active': toolFillMode === 'gradient' }" @click="toolFillMode = 'gradient'">{{ t('ppt.fill.gradient') }}</button>
              </div>
            </div>
            <template v-if="toolFillMode === 'solid'">
              <div class="pe-tool-item">
                <span class="pe-tool-label">{{ t('ppt.fill') }}</span>
                <label class="pe-color-swatch" :style="{ background: toolFillColor }">
                  <input v-model="toolFillColor" type="color" class="sr-only" />
                </label>
              </div>
            </template>
            <template v-else>
              <div class="pe-tool-item">
                <label class="pe-color-swatch" :style="{ background: toolGradientFrom }" :title="t('ppt.gradient.start')">
                  <input v-model="toolGradientFrom" type="color" class="sr-only" />
                </label>
                <span class="text-gray-300 text-xs px-0.5">&#8594;</span>
                <label class="pe-color-swatch" :style="{ background: toolGradientTo }" :title="t('ppt.gradient.end')">
                  <input v-model="toolGradientTo" type="color" class="sr-only" />
                </label>
              </div>
              <div class="pe-tool-item">
                <div class="pe-angle-dial" :title="t('ppt.gradient.drag_angle')" @mousedown="startAngleDrag">
                  <div class="pe-angle-needle" :style="{ transform: `rotate(${toolGradientAngle}deg)` }" />
                  <div class="pe-angle-dot" />
                </div>
                <span class="pe-tool-label">{{ toolGradientAngle }}°</span>
              </div>
            </template>
            <div class="pe-tool-item">
              <span class="pe-tool-label">{{ t('ppt.border') }}</span>
              <label class="pe-color-swatch" :style="{ background: toolStrokeColor }">
                <input v-model="toolStrokeColor" type="color" class="sr-only" />
              </label>
            </div>
            <div class="pe-tool-item">
              <span class="pe-tool-label">{{ t('ppt.opacity') }}</span>
              <input v-model.number="toolOpacity" type="range" min="0" max="100" class="pe-range" />
              <span class="pe-tool-value">{{ toolOpacity }}%</span>
            </div>
            <div class="pe-tool-item">
              <span class="pe-tool-label">{{ t('ppt.radius') }}</span>
              <input v-model.number="toolCornerRadius" type="number" min="0" max="100" class="pe-input-num w-12" />
            </div>
          </template>
          <span v-else class="text-xs text-gray-400 italic">{{ t('ppt.select_element') }}</span>
        </div>

        <div class="pe-toolbar-sep" />

        <!-- Add / Delete -->
        <div class="pe-tool-group">
          <button class="pe-btn pe-btn-outline" @click="addText">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M3 3h8M7 3v8"/></svg>
            {{ t('ppt.add_text') }}
          </button>
          <button class="pe-btn pe-btn-outline" @click="addRect">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="2" y="3" width="10" height="8" rx="1.5"/></svg>
            {{ t('ppt.rect') }}
          </button>
          <button class="pe-btn pe-btn-danger" :disabled="!selectedObj" @click="deleteSelected">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M3 4h8M5.5 4V3h3v1M4.5 4v7.5h5V4"/></svg>
          </button>
        </div>

        <div class="flex-1" />

        <!-- Page nav -->
        <div class="pe-tool-group">
          <button class="pe-btn pe-btn-ghost" :disabled="currentPageIndex === 0" @click="goToPage(currentPageIndex - 1)">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 3l-4 4 4 4"/></svg>
          </button>
          <span class="pe-page-label">{{ currentPageIndex + 1 }}<span class="text-gray-300"> / </span>{{ totalPages }}</span>
          <button class="pe-btn pe-btn-ghost" :disabled="currentPageIndex >= totalPages - 1" @click="goToPage(currentPageIndex + 1)">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 3l4 4-4 4"/></svg>
          </button>
        </div>

        <div class="pe-toolbar-sep" />

        <!-- Actions -->
        <div class="pe-tool-group">
          <button class="pe-btn pe-btn-secondary" :disabled="saving" @click="handleSave">
            {{ saving ? t('ppt.saving') : t('ui.save') }}
          </button>
          <span v-if="saveMessage" class="text-xs text-emerald-500 font-medium">{{ saveMessage }}</span>
          <button class="pe-btn pe-btn-outline" @click="enterFullscreen" :title="t('ppt.fullscreen_preview')">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M2 5V2h3M9 2h3v3M12 9v3h-3M5 12H2V9"/></svg>
            {{ t('ui.preview') }}
          </button>
          <button class="pe-btn pe-btn-primary" :disabled="exporting" @click="handleExport">
            {{ exporting ? t('ppt.exporting') : t('ppt.export_ppt') }}
          </button>
          <button class="pe-btn pe-btn-ghost" @click="emit('close')" :title="t('ppt.close_editor')">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg>
          </button>
        </div>
      </div>

      <!-- Canvas area -->
      <div ref="canvasWrapperEl" class="pe-canvas-area">
        <div class="pe-canvas-frame">
          <canvas ref="canvasEl" style="display:block" />
        </div>
      </div>
    </div>

    <!-- Fullscreen preview overlay -->
    <div v-if="isFullscreen" ref="fsOverlayEl" class="pe-fs-overlay" @click="exitFullscreen">
      <div class="pe-fs-canvas" @click.stop>
        <canvas ref="fsCanvasEl" style="display:block" />
      </div>
      <!-- Page indicator -->
      <div class="pe-fs-indicator">
        {{ fsPageIndex + 1 }} / {{ totalPages }}
      </div>
      <!-- Nav arrows -->
      <button v-if="fsPageIndex > 0" class="pe-fs-nav pe-fs-nav-left" @click.stop="fsPrev">
        <svg width="32" height="32" viewBox="0 0 32 32" fill="none" stroke="white" stroke-width="2.5" stroke-linecap="round"><path d="M20 8l-8 8 8 8"/></svg>
      </button>
      <button v-if="fsPageIndex < totalPages - 1" class="pe-fs-nav pe-fs-nav-right" @click.stop="fsNext">
        <svg width="32" height="32" viewBox="0 0 32 32" fill="none" stroke="white" stroke-width="2.5" stroke-linecap="round"><path d="M12 8l8 8-8 8"/></svg>
      </button>
      <!-- ESC hint -->
      <div class="pe-fs-hint">{{ t('ppt.press_esc') }}</div>
    </div>
  </div>
</template>

<style scoped>
/* ── Layout ────────────────────────────────────── */
.pe-overlay {
  position: fixed; inset: 0; z-index: 50;
  display: flex;
  background: rgba(15, 23, 42, 0.7);
  backdrop-filter: blur(8px);
}
.pe-sidebar {
  width: 200px; flex-shrink: 0;
  background: #f8fafc;
  border-right: 1px solid #e2e8f0;
  display: flex; flex-direction: column;
}
.pe-sidebar-header {
  padding: 14px 16px; font-size: 13px; font-weight: 600; color: #334155;
  border-bottom: 1px solid #e2e8f0;
  display: flex; align-items: center; gap: 8px;
}
.pe-badge {
  font-size: 11px; font-weight: 500;
  background: #e2e8f0; color: #64748b;
  padding: 1px 7px; border-radius: 10px;
}
.pe-slide-list {
  flex: 1; overflow-y: auto; padding: 8px;
}
.pe-slide-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px; margin-bottom: 4px;
  border-radius: 8px; cursor: pointer;
  transition: all 0.15s;
  border: 2px solid transparent;
}
.pe-slide-item:hover { background: #f1f5f9; }
.pe-slide-active {
  background: #eff6ff !important;
  border-color: #3b82f6;
}
.pe-slide-num {
  width: 24px; height: 24px; border-radius: 6px;
  background: #e2e8f0; color: #64748b;
  font-size: 11px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.pe-slide-active .pe-slide-num {
  background: #3b82f6; color: #fff;
}
.pe-slide-info { min-width: 0; }
.pe-slide-title {
  font-size: 12px; font-weight: 500; color: #475569;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.pe-slide-active .pe-slide-title { color: #1e40af; font-weight: 600; }

.pe-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }

/* ── Toolbar ───────────────────────────────────── */
.pe-toolbar {
  height: 48px; flex-shrink: 0;
  background: #fff; border-bottom: 1px solid #e2e8f0;
  display: flex; align-items: center;
  padding: 0 12px; gap: 6px;
}
.pe-toolbar-sep {
  width: 1px; height: 24px; background: #e2e8f0; margin: 0 4px;
}
.pe-tool-group {
  display: flex; align-items: center; gap: 6px;
}
.pe-tool-item {
  display: flex; align-items: center; gap: 4px;
}
.pe-tool-label {
  font-size: 11px; color: #94a3b8; font-weight: 500; user-select: none;
}
.pe-tool-value {
  font-size: 11px; color: #64748b; min-width: 28px;
}

/* ── Inputs ────────────────────────────────────── */
.pe-input-num {
  height: 28px; font-size: 12px;
  border: 1px solid #e2e8f0; border-radius: 6px;
  padding: 0 6px; text-align: center;
  outline: none; transition: border 0.15s;
}
.pe-input-num:focus { border-color: #3b82f6; }

.pe-color-swatch {
  width: 26px; height: 26px; border-radius: 6px;
  border: 2px solid #e2e8f0; cursor: pointer;
  display: block; transition: border-color 0.15s;
  box-shadow: 0 1px 2px rgba(0,0,0,0.06);
}
.pe-color-swatch:hover { border-color: #94a3b8; }

.pe-range {
  width: 72px; height: 28px;
  accent-color: #3b82f6;
}

/* ── Buttons ───────────────────────────────────── */
.pe-btn {
  display: inline-flex; align-items: center; gap: 4px;
  height: 30px; padding: 0 10px;
  font-size: 12px; font-weight: 500;
  border-radius: 7px; border: none; cursor: pointer;
  transition: all 0.15s; white-space: nowrap;
}
.pe-btn:disabled { opacity: 0.35; cursor: default; }

.pe-btn-primary {
  background: #10b981; color: #fff;
}
.pe-btn-primary:hover:not(:disabled) { background: #059669; }

.pe-btn-secondary {
  background: #3b82f6; color: #fff;
}
.pe-btn-secondary:hover:not(:disabled) { background: #2563eb; }

.pe-btn-outline {
  background: #fff; color: #475569;
  border: 1px solid #e2e8f0;
}
.pe-btn-outline:hover:not(:disabled) { background: #f8fafc; border-color: #cbd5e1; }

.pe-btn-danger {
  background: #fff; color: #ef4444;
  border: 1px solid #fecaca;
  padding: 0 8px;
}
.pe-btn-danger:hover:not(:disabled) { background: #fef2f2; }

.pe-btn-ghost {
  background: transparent; color: #64748b;
  padding: 0 6px;
}
.pe-btn-ghost:hover:not(:disabled) { background: #f1f5f9; color: #334155; }

.pe-btn-icon {
  width: 28px; height: 28px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 6px; border: 1px solid #e2e8f0;
  background: #fff; color: #64748b; cursor: pointer;
  transition: all 0.15s; font-size: 13px;
}
.pe-btn-icon:hover { background: #f1f5f9; }
.pe-btn-active {
  background: #1e293b !important; color: #fff !important;
  border-color: #1e293b !important;
}

.pe-btn-sm {
  height: 26px; padding: 0 8px;
  font-size: 11px; font-weight: 500;
  border-radius: 5px; border: 1px solid #e2e8f0;
  background: #fff; color: #64748b; cursor: pointer;
  transition: all 0.15s;
}
.pe-btn-sm:hover { background: #f1f5f9; }
.pe-btn-sm.pe-btn-active {
  background: #1e293b; color: #fff; border-color: #1e293b;
}

.pe-btn-group {
  display: flex; gap: 1px;
  background: #e2e8f0; border-radius: 6px; overflow: hidden;
}
.pe-btn-group > .pe-btn-icon,
.pe-btn-group > .pe-btn-sm { border-radius: 0; border: none; }

/* ── Angle dial ────────────────────────────────── */
.pe-angle-dial {
  width: 26px; height: 26px; border-radius: 50%;
  border: 2px solid #e2e8f0; background: #fff;
  position: relative; cursor: pointer;
  transition: border-color 0.15s;
}
.pe-angle-dial:hover { border-color: #94a3b8; }
.pe-angle-needle {
  position: absolute; width: 2px; height: 9px;
  background: #3b82f6; border-radius: 2px;
  left: 50%; top: 2px;
  transform-origin: bottom center;
  margin-left: -1px;
}
.pe-angle-dot {
  position: absolute; width: 4px; height: 4px;
  background: #3b82f6; border-radius: 50%;
  top: 50%; left: 50%;
  transform: translate(-50%, -50%);
}

/* ── Page nav ──────────────────────────────────── */
.pe-page-label {
  font-size: 12px; font-weight: 600; color: #475569;
  padding: 0 4px; min-width: 40px; text-align: center;
}

/* ── Canvas ────────────────────────────────────── */
.pe-canvas-area {
  flex: 1; overflow: hidden;
  display: flex; align-items: center; justify-content: center;
  background: #e5e7eb;
  background-image: radial-gradient(#d1d5db 1px, transparent 1px);
  background-size: 20px 20px;
}
.pe-canvas-frame {
  border-radius: 8px; overflow: hidden;
  box-shadow:
    0 20px 60px rgba(0, 0, 0, 0.2),
    0 0 0 1px rgba(0, 0, 0, 0.05);
}

/* ── Custom scrollbar ──────────────────────────── */
.pe-slide-list::-webkit-scrollbar { width: 5px; }
.pe-slide-list::-webkit-scrollbar-track { background: transparent; }
.pe-slide-list::-webkit-scrollbar-thumb {
  background: #cbd5e1; border-radius: 10px;
}
.pe-slide-list::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

/* ── Fullscreen preview ────────────────────────── */
.pe-fs-overlay {
  position: fixed; inset: 0; z-index: 100;
  background: #000;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer;
}
.pe-fs-canvas {
  cursor: default;
}
.pe-fs-indicator {
  position: absolute; bottom: 24px; left: 50%;
  transform: translateX(-50%);
  font-size: 14px; font-weight: 500; color: rgba(255,255,255,0.6);
  background: rgba(0,0,0,0.5); padding: 6px 16px;
  border-radius: 20px; backdrop-filter: blur(4px);
  user-select: none;
}
.pe-fs-hint {
  position: absolute; top: 20px; right: 24px;
  font-size: 12px; color: rgba(255,255,255,0.35);
  user-select: none;
}
.pe-fs-nav {
  position: absolute; top: 50%; transform: translateY(-50%);
  width: 48px; height: 48px;
  border-radius: 50%; border: none;
  background: rgba(255,255,255,0.1);
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: background 0.2s;
  backdrop-filter: blur(4px);
}
.pe-fs-nav:hover { background: rgba(255,255,255,0.2); }
.pe-fs-nav-left { left: 20px; }
.pe-fs-nav-right { right: 20px; }

/* ── sr-only for color inputs ──────────────────── */
.sr-only {
  position: absolute; width: 1px; height: 1px;
  padding: 0; margin: -1px; overflow: hidden;
  clip: rect(0, 0, 0, 0); border: 0;
}
</style>
