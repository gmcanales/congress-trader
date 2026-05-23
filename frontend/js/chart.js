class TradeChart {
  constructor(svgId, tooltipId) {
    this.svgId = svgId;
    this.tooltipId = tooltipId;
    this.margin = { top: 16, right: 24, bottom: 24, left: 64 };
    this.contextH = 56;
    this.contextGap = 24;
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
    this._drawContext();
    this._setupBrush();
    this._setupDragPan();
  }

  clear() {
    d3.select(`#${this.svgId}`).selectAll('*').remove();
  }

  // ── Setup ──────────────────────────────────────────────────────────────────

  _setup(W, H) {
    const m = this.margin;
    this.W = W - m.left - m.right;
    this.focusH = H - m.top - m.bottom - this.contextH - this.contextGap;

    const svg = d3.select(`#${this.svgId}`)
      .attr('width', W)
      .attr('height', H);
    svg.selectAll('*').remove();

    svg.append('defs').append('clipPath')
      .attr('id', 'price-clip')
      .append('rect')
      .attr('width', this.W)
      .attr('height', this.focusH + 6)
      .attr('y', -6);

    this.focus = svg.append('g')
      .attr('class', 'focus')
      .attr('transform', `translate(${m.left},${m.top})`);

    this.ctx = svg.append('g')
      .attr('class', 'context')
      .attr('transform', `translate(${m.left},${m.top + this.focusH + this.contextGap})`);
  }

  // ── Scales ────────────────────────────────────────────────────────────────

  _buildScales() {
    const ext = d3.extent(this.prices, d => d.date);
    this.xFull = d3.scaleTime().domain(ext).range([0, this.W]);
    this.xFocus = this.xFull.copy();
    this.yFull = this._yDomain(this.prices, this.focusH);
    this.yFocus = this.yFull.copy();
    this.xCtx = this.xFull.copy();
    this.yCtx = this._yDomain(this.prices, this.contextH);
  }

  _yDomain(data, height) {
    const lo = d3.min(data, d => d.low)  * 0.98;
    const hi = d3.max(data, d => d.high) * 1.02;
    return d3.scaleLinear().domain([lo, hi]).range([height, 0]);
  }

  // ── Focus chart ───────────────────────────────────────────────────────────

  _drawFocus() {
    this._drawGrid();
    this._drawArea(this.focus, this.xFocus, this.yFocus, this.focusH, 'focus-area');
    this._drawLine(this.focus, this.xFocus, this.yFocus, 'focus-line');
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

  // ── Context / brush ───────────────────────────────────────────────────────

  _drawContext() {
    const lineCtx = d3.line()
      .x(d => this.xCtx(d.date))
      .y(d => this.yCtx(d.close));

    this.ctx.append('path')
      .datum(this.prices)
      .attr('class', 'price-line context-line')
      .attr('d', lineCtx);

    this.ctx.append('g')
      .attr('class', 'x-axis')
      .attr('transform', `translate(0,${this.contextH})`)
      .call(d3.axisBottom(this.xCtx).ticks(8));
  }

  _setupBrush() {
    const self = this;
    this.brush = d3.brushX()
      .extent([[0, 0], [this.W, this.contextH]])
      .on('brush end', function (event) {
        if (!event.selection) return;
        const [x0, x1] = event.selection.map(self.xCtx.invert);
        self.xFocus.domain([x0, x1]);

        const visible = self.prices.filter(d => d.date >= x0 && d.date <= x1);
        if (visible.length > 1) {
          self.yFocus.domain([
            d3.min(visible, d => d.low)  * 0.98,
            d3.max(visible, d => d.high) * 1.02,
          ]);
        }
        self._updateFocus();
      });

    this.brushG = this.ctx.append('g')
      .attr('class', 'brush')
      .call(this.brush)
      .call(this.brush.move, this.xCtx.range());
  }

  _updateFocus() {
    const line = d3.line()
      .x(d => this.xFocus(d.date))
      .y(d => this.yFocus(d.close))
      .curve(d3.curveMonotoneX);

    const area = d3.area()
      .x(d => this.xFocus(d.date))
      .y0(this.focusH)
      .y1(d => this.yFocus(d.close))
      .curve(d3.curveMonotoneX);

    this.focus.select('.focus-line').attr('d', line);
    this.focus.select('.focus-area').attr('d', area);
    this.xAxisG.call(d3.axisBottom(this.xFocus).ticks(6));
    this.yAxisG.call(d3.axisLeft(this.yFocus).ticks(6).tickFormat(d => `$${d3.format(',.0f')(d)}`));
    this._updateGrid();
    this._drawMarkers();
  }

  _setupDragPan() {
    const self = this;
    let x0 = null;
    let domain0 = null;

    const drag = d3.drag()
      .on('start', function (event) {
        x0 = event.x;
        domain0 = self.xFocus.domain().slice();
      })
      .on('drag', function (event) {
        const dx = event.x - x0;
        const [r0, r1] = self.xFocus.range();
        const msPerPx = (domain0[1] - domain0[0]) / (r1 - r0);
        const shift = -dx * msPerPx;

        const [full0, full1] = self.xFull.domain();
        let d0 = new Date(domain0[0].getTime() + shift);
        let d1 = new Date(domain0[1].getTime() + shift);

        if (d0 < full0) { const over = full0 - d0; d0 = new Date(full0); d1 = new Date(d1.getTime() + over); }
        if (d1 > full1) { const over = d1 - full1; d1 = new Date(full1); d0 = new Date(d0.getTime() - over); }

        self.xFocus.domain([d0, d1]);
        self._updateFocus();
        self.brushG.call(self.brush.move, [self.xCtx(d0), self.xCtx(d1)]);
      });

    this.focus.append('rect')
      .attr('class', 'zoom-capture')
      .attr('width', this.W)
      .attr('height', this.focusH)
      .attr('fill', 'transparent')
      .call(drag);
  }
}
