/* Preserve conversation order while displaying dates in the reader’s time zone. */
((root) => {
  'use strict';
  const dayFormat = new Intl.DateTimeFormat(undefined, {dateStyle: 'medium'});

  function render(messages, timestamp, messageMarkup, dayMarkup) {
    let previousDay;
    return messages.flatMap(message => {
      const value = timestamp(message);
      const date = value == null ? null : new Date(value);
      const day = date && !Number.isNaN(date.getTime()) ? dayFormat.format(date) : null;
      const line = messageMarkup(message);
      if (!day || day === previousDay) return [line];
      previousDay = day;
      return [dayMarkup(day), line];
    });
  }

  root.StudioChatTimeline = {render};
})(window);
