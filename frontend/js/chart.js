class TradeChart {
  constructor(svgId, tooltipId) {
    this.svgId = svgId;
    this.tooltipId = tooltipId;
    this.margin = { top: 16, right: 24, bottom: 24, left: 64 };
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  render(priceData, transactions) {
    this.prices = priceData.map(d => ({ ...d, date: new Date(d.date) }));
    this.trades = transactions.map(d => ({ ...d, date: new Date(d.transaction_date) }));

    const container = document.getElementById(this.svgId).parentElement;
    const W = container.clientWidth;
    const H = container.clientHeight - 8; // small padding

    this._setup(W, H);
    this._buildScales();
    this._drawFocus();
    this._setupZoom();
  }

  clear() {
    d3.select(`#${this.svgId}`).selectAll('*').remove();
  }

  // ── Setup ──────────────────────────────────────────────────────────────────

  _setup(W, H) {
    const m = this.margin;
    this.W = W - m.left - m.right;
    this.focusH = H - m.top - m.bottom;

    this.svg = d3.select(`#${this.svgId}`)
      .attr('width', W)
      .attr('height', H);
    this.svg.selectAll('*').remove();

    this.svg.append('defs').append('clipPath')
      .attr('id', 'price-clip')
      .append('rect')
      .attr('width', this.W)
      .attr('height', this.focusH + 6)
      .attr('y', -6);

    this.focus = this.svg.append('g')
      .attr('class', 'focus')
      .attr('transform', `translate(${m.left},${m.top})`);
  }

  // ── Scales ────────────────────────────────────────────────────────────────

  _buildScales() {
    const ext = d3.extent(this.prices, d => d.date);
    this.xOrig = d3.scaleTime().domain(ext).range([0, this.W]);
    this.yOrig = this._yDomain(this.prices, this.focusH);
    this.xFocus = this.xOrig.copy();
    this.yFocus = this.yOrig.copy();
  }

  _yDomain(data, height) {
    const lo = d3.min(data, d => d.low)  * 0.98;
    const hi = d3.max(data, d => d.high) * 1.02;
    return d3.scaleLinear().domain([lo, hi]).range([height, 0]);
  }

  // ── Focus chart ───────────────────────────────────────────────────────────

  _drawFocus() {
    // Invisible capture rect goes first so markers stay on top and clickable.
    this.zoomRect = this.focus.append('rect')
      .attr('class', 'zoom-capture')
      .attr('width', this.W)
      .attr('height', this.focusH)
      .attr('fill', 'transparent');

    this._drawGrid();
    this.areaPath = this._drawArea(this.focus, this.xFocus, this.yFocus, this.focusH, 'focus-area');
    this.linePath = this._drawLine(this.focus, this.xFocus, this.yFocus, 'focus-line');
    this._drawMarkers();
    this._drawFocusAxes();
  }

  _drawGrid() {
    this.gridG = this.focus.append('g').attr('class', 'grid');
    this._updateGrid();
  }

  _updateGrid() {
    const ticks = this.yFocus.ticks(6);
    this.gridG.selectAll('line').remove();
    this.gridG.selectAll('line')
      .data(ticks)
      .join('line')
      .attr('x1', 0).attr('x2', this.W)
      .attr('y1', d => this.yFocus(d))
      .attr('y2', d => this.yFocus(d))
      .attr('stroke', 'var(--border)')
      .attr('stroke-dasharray', '2,3');
  }

  _drawArea(g, xScale, yScale, height, cls) {
    const area = d3.area()
      .x(d => xScale(d.date))
      .y0(height)
      .y1(d => yScale(d.close))
      .curve(d3.curveMonotoneX);
    return g.append('path')
      .datum(this.prices)
      .attr('class', `area ${cls}`)
      .attr('d', area)
      .attr('clip-path', 'url(#price-clip)');
  }

  _drawLine(g, xScale, yScale, cls) {
    const line = d3.line()
      .x(d => xScale(d.date))
      .y(d => yScale(d.close))
      .curve(d3.curveMonotoneX);
    return g.append('path')
      .datum(this.prices)
      .attr('class', `price-line ${cls}`)
      .attr('d', line)
      .attr('clip-path', 'url(#price-clip)');
  }

  _drawFocusAxes() {
    this.xAxisG = this.focus.append('g')
      .attr('class', 'x-axis')
      .attr('transform', `translate(0,${this.focusH})`)
      .call(d3.axisBottom(this.xFocus).ticks(6));

    this.yAxisG = this.focus.append('g')
      .attr('class', 'y-axis')
      .call(d3.axisLeft(this.yFocus).ticks(6).tickFormat(d => `$${d3.format(',.0f')(d)}`));
  }

  // ── Trade markers ─────────────────────────────────────────────────────────

  _drawMarkers() {
    if (this.markersG) this.markersG.remove();
    this.markersG = this.focus.append('g')
      .attr('class', 'markers-group')
      .attr('clip-path', 'url(#price-clip)');

    const tooltip = d3.select(`#${this.tooltipId}`);
    const self = this;

    this.trades.forEach(tx => {
      const px = this.xFocus(tx.date);
      const price = this._priceAt(tx.date);
      if (price === null) return;

      const py = this.yFocus(price);
      const isBuy = tx.transaction_type === 'purchase';
      const size = this._markerSize(tx.amount_max || tx.amount_min);
      const sym = d3.symbol().type(d3.symbolTriangle).size(size)();

      const offset = isBuy ? -10 : 10;
      const rot = isBuy ? 0 : 180;

      const confClass = tx.confidence === 'low' ? ' conf-low-marker' : '';
      this.markersG.append('path')
        .attr('d', sym)
        .attr('class', `marker ${isBuy ? 'buy' : 'sell'}${confClass}`)
        .attr('transform', `translate(${px},${py + offset}) rotate(${rot})`)
        .on('mouseover', (event) => {
          tooltip.classed('hidden', false).html(self._tooltipHtml(tx, price));
          self._positionTooltip(event, tooltip);
        })
        .on('mousemove', (event) => self._positionTooltip(event, tooltip))
        .on('mouseout', () => tooltip.classed('hidden', true));
    });
  }

  _priceAt(tradeDate) {
    if (!this.prices.length) return null;
    // Binary search for nearest price bar
    let closest = this.prices[0];
    let minDiff = Math.abs(tradeDate - closest.date);
    for (const bar of this.prices) {
      const diff = Math.abs(tradeDate - bar.date);
      if (diff < minDiff) { minDiff = diff; closest = bar; }
      if (diff === 0) break;
    }
    // Don't show markers more than 5 trading days off
    return minDiff <= 7 * 24 * 3600 * 1000 ? closest.close : null;
  }

  _markerSize(amount) {
    if (!amount) return 36;
    return Math.max(28, Math.min(160, 28 + Math.log10(amount / 500) * 28));
  }

  _tooltipHtml(tx, price) {
    const name = [tx.member?.first_name, tx.member?.last_name].filter(Boolean).join(' ');
    const isBuy = tx.transaction_type === 'purchase';
    const typeLabel = isBuy ? '▲ Buy' : '▼ Sell';
    const typeClass = isBuy ? 'buy-color' : 'sell-color';
    const dateFmt = d3.timeFormat('%b %d, %Y');
    const priceFmt = price != null ? `@ $${price.toFixed(2)}` : '';
    const conf = tx.confidence || 'high';
    const confLabel = { high: 'High', medium: 'Medium', low: 'Low' }[conf];
    return `
      <div class="tooltip-header ${typeClass}">${typeLabel} ${tx.ticker || ''}</div>
      <div class="tooltip-member">${name}</div>
      <div class="tooltip-date">${dateFmt(tx.date)} ${priceFmt}</div>
      <div class="tooltip-amount">${this._fmtAmount(tx.amount_min, tx.amount_max)}</div>
      ${tx.asset_name ? `<div class="tooltip-asset">${tx.asset_name}</div>` : ''}
      ${tx.owner ? `<div class="tooltip-asset">Owner: ${this._ownerLabel(tx.owner)}</div>` : ''}
      <div class="tooltip-asset">Parse confidence: <span class="conf-text-${conf}">${confLabel}</span></div>
    `;
  }

  _fmtAmount(min, max) {
    if (!min && !max) return 'Amount undisclosed';
    const fmt = v => v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${(v / 1e3).toFixed(0)}K`;
    return max ? `${fmt(min)} – ${fmt(max)}` : `> ${fmt(min)}`;
  }

  _ownerLabel(code) {
    return { SP: 'Spouse', JT: 'Joint', DC: 'Dependent Child', '': 'Self' }[code] ?? code;
  }

  _positionTooltip(event, tooltip) {
    const vpW = window.innerWidth;
    let x = event.clientX + 16;
    if (x + 210 > vpW) x = event.clientX - 226;
    tooltip.style('left', `${x}px`).style('top', `${event.clientY - 10}px`);
  }

  // ── Zoom (wheel + drag-pan, x and y) ──────────────────────────────────────

  _setupZoom() {
    const self = this;
    const zoom = d3.zoom()
      .scaleExtent([1, 50])
      .on('zoom', event => {
        self.xFocus = event.transform.rescaleX(self.xOrig);
        self.yFocus = event.transform.rescaleY(self.yOrig);
        self._redraw();
      });

    // Attach to SVG so wheel/drag events anywhere (including over markers)
    // trigger the same zoom behavior — matches the Analysis chart.
    this.svg.call(zoom);
  }

  _redraw() {
    const line = d3.line()
      .x(d => this.xFocus(d.date))
      .y(d => this.yFocus(d.close))
      .curve(d3.curveMonotoneX);

    const area = d3.area()
      .x(d => this.xFocus(d.date))
      .y0(this.focusH)
      .y1(d => this.yFocus(d.close))
      .curve(d3.curveMonotoneX);

    this.linePath.attr('d', line);
    this.areaPath.attr('d', area);
    this.xAxisG.call(d3.axisBottom(this.xFocus).ticks(6));
    this.yAxisG.call(d3.axisLeft(this.yFocus).ticks(6).tickFormat(d => `$${d3.format(',.0f')(d)}`));
    this._updateGrid();
    this._drawMarkers();
  }
}
