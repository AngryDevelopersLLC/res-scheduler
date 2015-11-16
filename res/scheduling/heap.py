import heapq


class Heap(object):
    def __init__(self):
        super(Heap, self).__init__()
        self._list = []

    def initialize(self, objs):
        self._list.extend(objs)
        heapq.heapify(self._list)

    def push(self, due_date, obj):
        heapq.heappush(self._list, (due_date, obj))

    def pop(self):
        return heapq.heappop(self._list)

    def size(self):
        return len(self._list)

    def min(self):
        return self._list[0]

    def __iter__(self):
        return iter(self._list)
