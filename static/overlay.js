"use strict";
(() => {
  const panel = new URLSearchParams(location.search).get('panel') === '1';
  if (panel) {
    document.body.classList.add('overlay-mode');
    document.title = 'GAMEPLAN · 技能悬浮面板';
    const main = document.querySelector('main');
    const content = document.createElement('div');
    content.className = 'overlay-content';
    content.append(document.querySelector('.enemy-selection'), document.querySelector('.enemy-subtitle-panel'));
    main.append(content);
    const reference = document.getElementById('enemy-reference');
    const referenceDetails = document.createElement('details');
    referenceDetails.innerHTML = '<summary>英雄资料与技能更正</summary>';
    reference.before(referenceDetails);
    referenceDetails.append(reference);
    const header = document.querySelector('.masthead');
    const tools = document.createElement('div');
    tools.className = 'overlay-tools';
    tools.innerHTML = '<b>敌方技能</b><button id="panel-settings" type="button" aria-expanded="false">采集设置</button><button id="panel-pause" type="button">暂停</button>';
    header.prepend(tools);
    const settings = document.querySelector('.controls');
    settings.hidden = true;
    const toggle = document.getElementById('panel-settings');
    toggle.addEventListener('click', () => {
      settings.hidden = !settings.hidden;
      toggle.setAttribute('aria-expanded', String(!settings.hidden));
    });
    const pause = document.getElementById('panel-pause');
    pause.addEventListener('click', () => {
      const paused = document.getElementById('monitor-pause').disabled;
      document.getElementById(paused ? 'monitor-start' : 'monitor-pause').click();
    });
    const sync = () => {pause.textContent = document.getElementById('monitor-pause').disabled ? '开始' : '暂停';};
    new MutationObserver(sync).observe(document.getElementById('monitor-pause'), {attributes:true, attributeFilter:['disabled']});
    window.gameplanEnemies?.tick();
    addEventListener('DOMContentLoaded', () => {
      if (!new URLSearchParams(location.search).has('source') && !localStorage.getItem('gameplan-screen-source')) toggle.click();
    });
  } else {
    const button = document.createElement('button');
    button.id = 'panel-open';
    button.type = 'button';
    button.textContent = '打开置顶小面板';
    document.querySelector('.control-row .buttons').prepend(button);
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const result = await api('/api/monitor/panel/open', {
          source_id: document.getElementById('screen-source').value || null,
          region: document.getElementById('capture-region').value
        });
        if (result.opened) window.gameplanMonitor.stop('已切换到置顶小面板');
        toast(result.opened ? '已启动置顶小面板' : '置顶小面板已经打开');
      } catch (error) { toast(error.message, true); }
      finally { button.disabled = false; }
    });
  }
})();
